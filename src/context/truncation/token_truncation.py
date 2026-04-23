from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from schemas.types import BudgetResult, LLMMessage, LLMRequest, ALL_ROLES
from context.estimator.token_estimator import BaseTokenEstimator, TokenEstimation
from context.budget.token_budget_manager import BaseTokenBudgetManager
from utils.log.log import Logger, zap

if TYPE_CHECKING:
    from llm.llm_api import BaseLLMClient
    from config.config import JsonConfig

# ===========================================================================
# Base class
# ===========================================================================

class ContextTruncator(ABC):
    @abstractmethod
    def truncate(self, request: LLMRequest, total_budget: int, estimator: BaseTokenEstimator) -> TruncationResult:
        ...

# ===========================================================================
# ReAct-aware truncation
# ===========================================================================

@dataclass
class TruncationResult:
    request: LLMRequest
    compacted_messages: list[LLMMessage] | None  # None = no truncation needed


@dataclass
class ReasoningUnit:
    assistant_msg: LLMMessage
    tool_msgs: list[LLMMessage] = field(default_factory=list)


@dataclass(frozen=True)
class ReActTruncationConfig:
    tool_arg_max_chars: int = 300
    tool_result_max_chars: int = 500
    summary_provider: str = "deepseek"


def _parse_reasoning_units(messages: list[LLMMessage]) -> list[ReasoningUnit]:
    """Group assistant+tool message sequences into ReasoningUnits."""
    units: list[ReasoningUnit] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.role == "assistant" and msg.metadata.get("tool_calls"):
            tool_call_ids: set[str] = {
                tc["llm_raw_tool_call_id"]
                for tc in msg.metadata["tool_calls"]
                if tc.get("llm_raw_tool_call_id")
            }
            unit = ReasoningUnit(assistant_msg=msg)
            i += 1
            while i < len(messages) and messages[i].role == "tool":
                tid = messages[i].metadata.get("llm_raw_tool_call_id")
                if tid in tool_call_ids:
                    unit.tool_msgs.append(messages[i])
                    i += 1
                else:
                    break
            units.append(unit)
        else:
            i += 1
    return units


def _unit_to_messages(unit: ReasoningUnit) -> list[LLMMessage]:
    return [unit.assistant_msg, *unit.tool_msgs]


def _unit_tool_signature(unit: ReasoningUnit) -> str:
    """Stable string key for dedup: tool names + sorted arguments."""
    calls = unit.assistant_msg.metadata.get("tool_calls", [])
    parts = []
    for tc in calls:
        parts.append(f"{tc.get('name')}:{json.dumps(tc.get('arguments', {}), sort_keys=True)}")
    return "|".join(parts)


def _has_failed_tool(unit: ReasoningUnit) -> bool:
    for msg in unit.tool_msgs:
        if msg.metadata.get("success") is False:
            return True
    return False


class ReActContextTruncator(ContextTruncator):
    def __init__(
        self,
        budget_manager: BaseTokenBudgetManager,
        llm_client_factory: Callable[[str], BaseLLMClient],
        logger: Logger,
        config: ReActTruncationConfig | None = None,
    ) -> None:
        self._budget_manager = budget_manager
        self._llm_client_factory = llm_client_factory
        self._logger = logger
        self._cfg = config or ReActTruncationConfig()

    def truncate(self, request: LLMRequest, total_budget: int, effective_estimator: BaseTokenEstimator) -> TruncationResult:
        if (effective_estimator is None) or (request is None) or (0 == total_budget):
            raise ValueError("Effective estimator, request, and tool budget must be provided and non-zero")

        budget = self._budget_manager.allocate(total_budget)
        estimation = effective_estimator.estimate(request)
        msgs = list(request.messages)

        #TODO system and user truncation strategies if needed

        assistant_budget = budget.role_budgets.get("assistant").token_budget
        tool_budget = budget.role_budgets.get("tool").token_budget

        self._logger.info(
            "Truncation check",
            assistant_tokens=estimation["assistant"],
            assistant_budget=assistant_budget,
            tool_tokens=estimation["tool"],
            tool_budget=tool_budget,
        )

        tokens_before = estimation["assistant"] + estimation["tool"]
        msgs_before = len(msgs)

        if estimation["assistant"] < assistant_budget and estimation["tool"] < tool_budget:
            self._logger.info("No truncation needed, context within budget.")
            return TruncationResult(request=request, compacted_messages=None)

        def fits(m: list[LLMMessage], role: str | list[str] | None) -> bool:
            est = effective_estimator.estimate(LLMRequest(messages=m), role)
            if role is None:
                role = list(ALL_ROLES)
            
            return all(est[r] <= budget.role_budgets.get(r).token_budget for r in (role if isinstance(role, list) else [role]))

        def _log_truncation_result(strategy: str, msgs_after: list[LLMMessage]) -> None:
            est_after = effective_estimator.estimate(LLMRequest(messages=msgs_after), ["assistant", "tool"])
            tokens_after = est_after["assistant"] + est_after["tool"]
            ratio = ((tokens_before - tokens_after) / tokens_before) if tokens_before > 0 else 0.0
            self._logger.info(
                f"{strategy} resolved budget",
                msgs_before=msgs_before,
                msgs_after=len(msgs_after),
                msgs_dropped=msgs_before - len(msgs_after),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                tokens_dropped=tokens_before - tokens_after,
                truncation_ratio=f"{ratio:.2%}",
            )

        msgs = self._strategy_a_dedup(msgs)
        if fits(msgs, ["assistant", "tool"]):
            _log_truncation_result("Strategy A (dedup)", msgs)
            return self._make_result(request, msgs)

        msgs = self._strategy_b_remove_failed(msgs)
        if fits(msgs, ["assistant", "tool"]):
            _log_truncation_result("Strategy B (remove failed)", msgs)
            return self._make_result(request, msgs)

        # C and D are applied selectively based on which role is over budget
        est = effective_estimator.estimate(LLMRequest(messages=msgs), ["assistant", "tool"])
        if est["assistant"] > assistant_budget:
            msgs = self._strategy_c_trim_args(msgs)
        if est["tool"] > tool_budget:
            msgs = self._strategy_d_trim_results(msgs)
        if fits(msgs, ["assistant", "tool"]):
            _log_truncation_result("Strategy C/D (trim args/results)", msgs)
            return self._make_result(request, msgs)

        if dropped := self._strategy_e_binary_drop(msgs, budget, effective_estimator):
            if fits(dropped, ["assistant", "tool"]):
                _log_truncation_result("Strategy E (binary drop)", dropped)
                return self._make_result(request, dropped)

        if summarized := self._strategy_f_summarize(msgs):
            msgs = summarized
            if fits(msgs, ["assistant", "tool"]):
                _log_truncation_result("Strategy F (summarize)", msgs)
                return self._make_result(request, msgs)

        if dropped := self._strategy_e_binary_drop(msgs, budget, effective_estimator):
            msgs = dropped
            if not fits(msgs, ["assistant", "tool"]):
                est_after = effective_estimator.estimate(LLMRequest(messages=msgs), ["assistant", "tool"])
                tokens_after = est_after["assistant"] + est_after["tool"]
                ratio = (tokens_before - tokens_after) / tokens_before if tokens_before > 0 else 0.0
                self._logger.warning(
                    "All truncation strategies exhausted but context is still over budget",
                    msgs_before=msgs_before,
                    msgs_after=len(msgs),
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    truncation_ratio=f"{ratio:.2%}",
                )

        return self._make_result(request, msgs)

    # ------------------------------------------------------------------
    # Strategy A: remove consecutive duplicate reasoning units
    # ------------------------------------------------------------------

    def _strategy_a_dedup(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        units = _parse_reasoning_units(messages)
        if len(units) < 2:
            return messages

        drop_units: set[int] = set()
        for i in range(len(units) - 1):
            if _unit_tool_signature(units[i]) == _unit_tool_signature(units[i + 1]):
                drop_units.add(i)

        if not drop_units:
            return messages

        self._logger.info("Strategy A: removing duplicate reasoning units", dropped=len(drop_units))
        keep_msgs = {id(m) for i, u in enumerate(units) if i not in drop_units for m in _unit_to_messages(u)}
        unit_msg_ids = {id(m) for u in units for m in _unit_to_messages(u)}
        return [m for m in messages if id(m) not in unit_msg_ids or id(m) in keep_msgs]

    # ------------------------------------------------------------------
    # Strategy B: remove failed reasoning units
    # ------------------------------------------------------------------

    def _strategy_b_remove_failed(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        units = _parse_reasoning_units(messages)
        failed_ids = {id(m) for u in units if _has_failed_tool(u) for m in _unit_to_messages(u)}
        if not failed_ids:
            return messages
        return [m for m in messages if id(m) not in failed_ids]

    # ------------------------------------------------------------------
    # Strategy C: trim oversized tool call arguments
    # ------------------------------------------------------------------

    def _strategy_c_trim_args(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        result: list[LLMMessage] = []
        limit = self._cfg.tool_arg_max_chars
        for msg in messages:
            if msg.role != "assistant" or not msg.metadata.get("tool_calls"):
                result.append(msg)
                continue
            new_calls = []
            changed = False
            for tc in msg.metadata["tool_calls"]:
                args = tc.get("arguments", {})
                new_args = {}
                for k, v in args.items():
                    if isinstance(v, str) and len(v) > limit:
                        new_args[k] = v[:limit] + "(trimmed because too long)"
                        changed = True
                    else:
                        new_args[k] = v
                new_calls.append({**tc, "arguments": new_args})
            if changed:
                result.append(LLMMessage(
                    role=msg.role,
                    content=msg.content,
                    metadata={**msg.metadata, "tool_calls": new_calls},
                ))
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Strategy D: trim oversized tool results
    # ------------------------------------------------------------------

    def _strategy_d_trim_results(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        result: list[LLMMessage] = []
        limit = self._cfg.tool_result_max_chars
        for msg in messages:
            if msg.role == "tool" and len(msg.content) > limit:
                result.append(LLMMessage(
                    role=msg.role,
                    content=msg.content[:limit] + "(trimmed because too long)",
                    metadata=msg.metadata,
                ))
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Strategy E: binary-search minimum drop of middle units
    # ------------------------------------------------------------------

    def _strategy_e_binary_drop(
        self,
        messages: list[LLMMessage],
        budget: BudgetResult,
        estimator: BaseTokenEstimator,
    ) -> list[LLMMessage] | None:
        units = _parse_reasoning_units(messages)
        if len(units) <= 2:
            return None

        middle_units = units[1:-1]
        max_drop = max(1, len(middle_units) // 10)

        lo, hi = 1, max_drop
        best: list[LLMMessage] | None = None

        while lo <= hi:
            k = (lo + hi) // 2
            candidate = self._drop_oldest_k(messages, units, middle_units, k)
            if self._fits(candidate, budget, estimator):
                best = candidate
                hi = k - 1
            else:
                lo = k + 1

        return best

    def _drop_oldest_k(
        self,
        messages: list[LLMMessage],
        all_units: list[ReasoningUnit],
        middle_units: list[ReasoningUnit],
        k: int,
    ) -> list[LLMMessage]:
        drop_ids = {id(m) for u in middle_units[:k] for m in _unit_to_messages(u)}
        unit_ids = {id(m) for u in all_units for m in _unit_to_messages(u)}
        return [m for m in messages if id(m) not in unit_ids or id(m) not in drop_ids]

    # ------------------------------------------------------------------
    # Strategy F: LLM summary of middle 30% units
    # ------------------------------------------------------------------

    def _strategy_f_summarize(
        self,
        messages: list[LLMMessage],
    ) -> list[LLMMessage] | None:
        units = _parse_reasoning_units(messages)
        if len(units) < 3:
            return None

        n = len(units)
        start = max(1, n // 2 - n // 6)
        end = min(n - 1, start + max(1, n * 3 // 10))
        summary_units = units[start:end]
        if not summary_units:
            return None

        summary_msgs = [m for u in summary_units for m in _unit_to_messages(u)]
        summary_msg = self._call_summary_llm(summary_msgs)
        if summary_msg is None:
            return None

        summary_ids = {id(m) for m in summary_msgs}
        result: list[LLMMessage] = []
        inserted = False
        for m in messages:
            if id(m) in summary_ids:
                if not inserted:
                    result.append(summary_msg)
                    inserted = True
            else:
                result.append(m)
        return result

    def _call_summary_llm(
        self,
        msgs_to_summarize: list[LLMMessage],
    ) -> LLMMessage | None:
        try:
            client = self._llm_client_factory(self._cfg.summary_provider)
            history_text = "\n".join(
                f"[{m.role}] {m.content}" for m in msgs_to_summarize
            )
            summary_request = LLMRequest(
                system_prompt=(
                    "You are a context compressor. Summarize the following reasoning steps "
                    "into a single concise assistant message. Preserve key facts, tool results, "
                    "and conclusions. Output only the summary text, no preamble."
                ),
                messages=[LLMMessage(role="user", content=history_text)],
                tools=[],
            )
            response = client.generate(summary_request)
            return LLMMessage(
                role="assistant",
                content=response.assistant_message.content,
                metadata={"summarized": True},
            )
        except Exception as exc:
            self._logger.error("Strategy F: summary LLM call failed", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fits(self, messages: list[LLMMessage], budget: BudgetResult, estimator: BaseTokenEstimator) -> bool:
        candidate = LLMRequest(
            messages=messages,
        )
        estimation = estimator.estimate(candidate)
        return estimation["total"] <= budget.available_tokens

    def _make_result(self, request: LLMRequest, messages: list[LLMMessage]) -> TruncationResult:
        new_request = LLMRequest(
            system_prompt=request.system_prompt,
            messages=messages,
            tools=request.tools,
        )
        return TruncationResult(request=new_request, compacted_messages=messages)


class TruncatorFactory:
    @classmethod
    def create(
        cls,
        strategy: str,
        budget_manager: BaseTokenBudgetManager,
        llm_client_factory: Callable[[str], BaseLLMClient],
        logger: Logger,
        config: JsonConfig | None = None,
    ) -> ContextTruncator:
        if strategy == "react":
            trunc_cfg = ReActTruncationConfig(
                tool_arg_max_chars=int(config.get("context_truncation.react.tool_arg_max_chars", 300)) if config is not None else 300,
                tool_result_max_chars=int(config.get("context_truncation.react.tool_result_max_chars", 500)) if config is not None else 500,
                summary_provider=(
                    config.get("context_truncation.react.summary_provider", "deepseek")
                    if config is not None else "deepseek"
                ),
            )
            return ReActContextTruncator(budget_manager, llm_client_factory, logger, trunc_cfg)
        raise ValueError(f"Unknown truncation strategy: {strategy!r}")
