from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from schemas.types import BudgetResult, ChatMessage, LLMRequest
from context.estimator.token_estimator import TokenEstimation


@dataclass(frozen=True)
class TruncationConfig:
    # Messages at head/tail that are completely protected (no drop, no content change)
    head_protect: int = 2
    tail_protect: int = 4
    # Phase-1 content truncation limits (chars, not tokens) for middle messages
    tool_max_chars: int = 500
    assistant_max_chars: int = 300


class Summarizer(Protocol):
    """Optional LLM-based summarizer injected for Phase-3 summary of dropped middle."""
    def summarize(self, messages: list[ChatMessage]) -> ChatMessage: ...


class ContextTruncator:
    """
    Progressive context truncator with a sliding-window protection strategy.

    Truncation is applied only to the "middle" slice of messages:
        messages = head (protected) + middle (truncatable) + tail (protected)

    Three phases, applied in order until each role fits its budget:
        Phase 1 – Content truncation  : shorten tool/assistant content (least destructive)
        Phase 2 – Message dropping    : drop oldest messages, role priority tool > assistant > user
        Phase 3 – LLM summary         : replace remaining middle with a single summary (optional)

    Time-decay is implicit: within middle, oldest messages (index 0) are processed first.
    """

    def __init__(
        self,
        count_fn: Callable[[str], int],
        config: TruncationConfig | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self._count = count_fn
        self._cfg = config or TruncationConfig()
        self._summarizer = summarizer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def truncate(
        self,
        request: LLMRequest,
        budget: BudgetResult,
        estimation: TokenEstimation,
    ) -> LLMRequest:
        if not self._needs_truncation(budget, estimation):
            return request

        n = len(request.messages)
        head_end = min(self._cfg.head_protect, n)
        tail_start = max(head_end, n - self._cfg.tail_protect)

        head = request.messages[:head_end]
        middle = list(request.messages[head_end:tail_start])
        tail = request.messages[tail_start:]

        middle = self._apply_phases(head, middle, tail, budget)

        return LLMRequest(
            system_prompt=request.system_prompt,
            messages=head + middle + tail,
            tools=request.tools,
        )

    # ------------------------------------------------------------------
    # Phase orchestration
    # ------------------------------------------------------------------

    def _apply_phases(
        self,
        head: list[ChatMessage],
        middle: list[ChatMessage],
        tail: list[ChatMessage],
        budget: BudgetResult,
    ) -> list[ChatMessage]:
        if not middle:
            return middle

        # Phase 1: content truncation — tool first (most verbose), then assistant
        middle = self._truncate_content(middle, "tool", self._cfg.tool_max_chars)
        middle = self._truncate_content(middle, "assistant", self._cfg.assistant_max_chars)

        # Phase 2: message dropping — tool (most expendable) → assistant → user (least expendable)
        # keep_last=1 for assistant: always retain the most recent reasoning step
        for role, keep_last in [("tool", 0), ("assistant", 1), ("user", 0)]:
            middle = self._drop_role(head, middle, tail, role, budget, keep_last=keep_last)

        # Phase 3: LLM summary — replace remaining middle with a single summary message
        if self._summarizer is not None and middle:
            all_msgs = head + middle + tail
            if self._total_tokens(all_msgs) > budget.available_tokens:
                summary = self._summarizer.summarize(middle)
                middle = [summary]

        return middle

    # ------------------------------------------------------------------
    # Phase 1: content truncation
    # ------------------------------------------------------------------

    def _truncate_content(
        self,
        messages: list[ChatMessage],
        role: str,
        max_chars: int,
    ) -> list[ChatMessage]:
        result: list[ChatMessage] = []
        for msg in messages:
            if msg.role == role and len(msg.content) > max_chars:
                content = msg.content[:max_chars] + " ...[truncated]"
                result.append(ChatMessage(
                    role=msg.role,
                    content=content,
                    metadata={**msg.metadata, "truncated": True},
                ))
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Phase 2: message dropping
    # ------------------------------------------------------------------

    def _drop_role(
        self,
        head: list[ChatMessage],
        middle: list[ChatMessage],
        tail: list[ChatMessage],
        role: str,
        budget: BudgetResult,
        keep_last: int = 0,
    ) -> list[ChatMessage]:
        role_budget = budget.role_budgets.get(role)
        if role_budget is None:
            return middle

        # Measure tokens for this role across all three sections
        current = (
            self._role_tokens(head, role)
            + self._role_tokens(middle, role)
            + self._role_tokens(tail, role)
        )
        if current <= role_budget.token_budget:
            return middle

        # Candidate indices in middle, oldest first (time-decay: oldest = most expendable)
        role_indices = [i for i, m in enumerate(middle) if m.role == role]
        droppable = role_indices[: max(0, len(role_indices) - keep_last)]

        # Special case: never drop the first user message in the entire conversation
        # (it carries the original task description)
        if role == "user" and not any(m.role == "user" for m in head):
            first_user = next((i for i in droppable), None)
            if first_user is not None:
                droppable = droppable[1:]

        drop_set: set[int] = set()
        for idx in droppable:
            if current <= role_budget.token_budget:
                break
            current -= self._count(middle[idx].content)
            drop_set.add(idx)

        return [m for i, m in enumerate(middle) if i not in drop_set]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _needs_truncation(self, budget: BudgetResult, estimation: TokenEstimation) -> bool:
        return any(
            estimation.get(role, 0) > rb.token_budget
            for role, rb in budget.role_budgets.items()
        )

    def _role_tokens(self, messages: list[ChatMessage], role: str) -> int:
        return sum(self._count(m.content) for m in messages if m.role == role)

    def _total_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(self._count(m.content) for m in messages)
