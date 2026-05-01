from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from schemas import LLMMessage, LLMResponse, ToolCall


class NextDecisionType(str, Enum):
    TOOL_CALL            = "TOOL_CALL"
    FINAL_ANSWER         = "FINAL_ANSWER"
    CONTINUE             = "CONTINUE"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"


@dataclass(frozen=True)
class NextDecision:
    decision_type: NextDecisionType
    tool_calls: list[ToolCall] = field(default_factory=list)
    answer: str = ""
    message: str = ""
    assistant_message: LLMMessage | None = None
    raw_response: LLMResponse | None = None
