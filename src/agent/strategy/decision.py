from __future__ import annotations

from dataclasses import dataclass

from schemas import AgentError, ChatMessage, ToolCall


@dataclass
class InvokeTools:
    assistant_message: ChatMessage
    tool_calls: list[ToolCall]


@dataclass
class FinalAnswer:
    message: ChatMessage


@dataclass
class ResponseTruncated:
    message: ChatMessage
    error: AgentError


StrategyDecision = InvokeTools | FinalAnswer | ResponseTruncated
