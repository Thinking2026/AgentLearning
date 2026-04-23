from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from schemas.errors import AgentError

#本文件引入的类型只能依赖内置类型或者文件中已经引入的类型，不能依赖其他文件中定义的类型，否则会导致循环依赖问题

UIRole = Literal["user", "assistant"]
LLMRole = Literal["user", "assistant", "tool"]
ALL_ROLES = ("system", "user", "assistant", "tool")


@dataclass(slots=True)
class UIMessage:
    """Message for UI/user interaction between user_thread and agent_thread."""
    role: UIRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMMessage:
    """Message conforming to LLM API spec, used in conversation history and LLM calls."""
    role: LLMRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


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
    messages: list[LLMMessage]
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class LLMResponse:
    assistant_message: LLMMessage
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
    user_messages: list[UIMessage] = field(default_factory=list)
    error: AgentError | None = None
    task_completed: bool = False

@dataclass(slots=True)
class RoleBudget:
    role: str
    ratio: float
    token_budget: int

@dataclass(slots=True)
class BudgetResult:
    strategy: str
    total_budget: int
    reserve_ratio: float
    reserved_tokens: int        # for LLM response + future summary calls
    available_tokens: int       # total_budget - reserved_tokens
    role_budgets: dict[str, RoleBudget] = field(default_factory=dict)
