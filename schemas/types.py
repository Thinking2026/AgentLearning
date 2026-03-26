from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from schemas.errors import AgentError


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


ChatRole = Literal["user", "assistant", "conversation"]


@dataclass(slots=True)
class ChatMessage:
    role: ChatRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_roles = {"user", "assistant", "conversation"}
        if self.role not in valid_roles:
            raise ValueError(f"Unsupported chat role: {self.role}")


@dataclass(slots=True)
class SystemMessage:
    command: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


ThreadMessage = ChatMessage | SystemMessage


@dataclass(slots=True)
class AgentEvent:
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(slots=True)
class ToolResult:
    call_id: str
    output: str
    success: bool = True
    error: AgentError | None = None


@dataclass(slots=True)
class LLMRequest:
    system_prompt: str
    messages: list[ChatMessage]
    tools: list[dict[str, Any]]
    context: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LLMResponse:
    assistant_message: ChatMessage
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"
