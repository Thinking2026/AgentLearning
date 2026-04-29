from __future__ import annotations

from dataclasses import dataclass

from schemas import AgentError, LLMMessage, ToolCall, UIMessage


@dataclass
class InvokeTools:
    assistant_message: LLMMessage
    tool_calls: list[ToolCall]


@dataclass
class FinalAnswer:
    message: UIMessage


@dataclass
class ResponseTruncated:
    message: UIMessage
    error: AgentError


StrategyDecision = InvokeTools | FinalAnswer | ResponseTruncated
