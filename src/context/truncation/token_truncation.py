from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable 

from schemas.types import BudgetResult, LLMMessage, LLMRequest
from context.estimator.token_estimator import BaseTokenEstimator, TokenEstimation
from context.budget.token_budget_manager import BaseTokenBudgetManager

if TYPE_CHECKING:
    from llm.llm_api import BaseLLMClient
    from config.config import JsonConfig

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


class ReActContextTruncator:
    def __init__(
        self,
        budget_manager: BaseTokenBudgetManager,
        estimator: BaseTokenEstimator,
        llm_client_factory: Callable[[str], BaseLLMClient],
        config: ReActTruncationConfig | None = None,
    ) -> None:
        self._budget_manager = budget_manager
        self._estimator = estimator
        self._llm_client_factory = llm_client_factory
        self._cfg = config or ReActTruncationConfig()

    def truncate(self, request: LLMRequest, total_budget: int) -> TruncationResult:
        budget = self._budget_manager.allocate(total_budget)
        estimation = self._estimator.estimate(request)
        if estimation["total"] <= budget.available_tokens:
            return TruncationResult(request=request, compacted_messages=None)

        msgs = list(request.messages)

        # Strategies A-D operate on the full message list (mutate copies)
        msgs = self._strategy_a_dedup(msgs)
        msgs = self._strategy_b_remove_failed(msgs)
        msgs = self._strategy_c_trim_args(msgs)
        msgs = self._strategy_d_trim_results(msgs)

        if self._fits(request, msgs, budget):
            return self._make_result(request, msgs)

        # Strategy E: binary-search drop of middle units
        msgs_e = self._strategy_e_binary_drop(request, msgs, budget)
        if msgs_e is not None:
            return self._make_result(request, msgs_e)

        # Strategy F: LLM summary of middle 30% units, then retry E
        msgs_f = self._strategy_f_summarize(request, msgs, budget)
        if msgs_f is not None:
            msgs_fe = self._strategy_e_binary_drop(request, msgs_f, budget)
            final = msgs_fe if msgs_fe is not None else msgs_f
            return self._make_result(request, final)

        # Fallback: return best-effort (E result or current msgs)
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
        request: LLMRequest,
        messages: list[LLMMessage],
        budget: BudgetResult,
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
            if self._fits(request, candidate, budget):
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
        request: LLMRequest,
        messages: list[LLMMessage],
        budget: BudgetResult,
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
        summary_msg = self._call_summary_llm(request, summary_msgs)
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
        original_request: LLMRequest,
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
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fits(self, request: LLMRequest, messages: list[LLMMessage], budget: BudgetResult) -> bool:
        candidate = LLMRequest(
            system_prompt=request.system_prompt,
            messages=messages,
            tools=request.tools,
        )
        estimation = self._estimator.estimate(candidate)
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
        estimator: BaseTokenEstimator,
        llm_client_factory: Callable[[str], BaseLLMClient],
        config: JsonConfig | None = None,
    ) -> ReActContextTruncator:
        if strategy == "react":
            summary_provider = (
                config.get("context_truncation.react.summary_provider", "deepseek")
                if config is not None else "deepseek"
            )
            trunc_cfg = ReActTruncationConfig(summary_provider=summary_provider)
            return ReActContextTruncator(budget_manager, estimator, llm_client_factory, trunc_cfg)
        raise ValueError(f"Unknown truncation strategy: {strategy!r}")
