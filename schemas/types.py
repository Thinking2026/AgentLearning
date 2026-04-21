from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from schemas.errors import AgentError
from utils.timezone import isoformat

#本文件引入的类型只能依赖内置类型或者文件中已经引入的类型，不能依赖其他文件中定义的类型，否则会导致循环依赖问题

ChatRole = Literal["user", "assistant", "tool"]

@dataclass(slots=True)
class ChatMessage:
    role: ChatRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_roles = {"user", "assistant", "tool"}
        if self.role not in valid_roles:
            raise ValueError(f"Unsupported chat role: {self.role}")

@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    llm_raw_tool_call_id: str | None = None


@dataclass(slots=True)
class ToolResult:
    output: str
    llm_raw_tool_call_id: str | None = None
    success: bool = True
    error: AgentError | None = None


@dataclass(slots=True)
class LLMRequest:
    system_prompt: str
    messages: list[ChatMessage]
    tools: list[dict[str, Any]]


@dataclass(slots=True)
class LLMResponse:
    assistant_message: ChatMessage
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"


@dataclass(slots=True)
class SQLQueryRequest:
    statement: str
    database: str | None = None
    params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None
    max_rows: int = 100


@dataclass(slots=True)
class VectorSearchRequest:
    query: str
    collection: str | None = None
    top_k: int = 3
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KeyValueGetRequest:
    key: str


@dataclass(slots=True)
class KeyValueSetRequest:
    key: str
    value: Any
    ttl_seconds: int | None = None


@dataclass(slots=True)
class AgentExecutionResult:
    user_messages: list[ChatMessage] = field(default_factory=list)
    error: AgentError | None = None
    task_completed: bool = False
