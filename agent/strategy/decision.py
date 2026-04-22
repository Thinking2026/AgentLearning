from __future__ import annotations

from dataclasses import dataclass

from schemas import AgentError, LLMMessage, ToolCall


@dataclass
class InvokeTools:
    assistant_message: LLMMessage
    tool_calls: list[ToolCall]


@dataclass
class FinalAnswer:
    message: LLMMessage


@dataclass
class ResponseTruncated:
    message: LLMMessage
    error: AgentError


StrategyDecision = InvokeTools | FinalAnswer | ResponseTruncated
