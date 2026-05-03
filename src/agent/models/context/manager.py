from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from schemas import LLMMessage, LLMRequest
from schemas.types import LLMRole

if TYPE_CHECKING:
    from collections.abc import Callable
    from agent.models.context.estimator.token_estimator import BaseTokenEstimator
    from agent.models.context.truncation.token_truncation import ContextTruncator as _ContextTruncator
    from config.config import JsonConfig
    from llm.llm_gateway import BaseLLMClient


@dataclass(frozen=True)
class ContextMessage:
    id: str
    role: LLMRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ContextWindow:
    system_prompt: str
    messages: list[LLMMessage]
    token_count: int = 0

@dataclass(frozen=True)
class AgentContext:
    system_prompt: str
    tool_schemas: str
    messages: list[ContextMessage]
    stage_index: list[int]
    variables: dict[str, Any]


class ContextTruncator(Protocol):
    def truncate(self, request: LLMRequest, total_budget: int, estimator: Any) -> Any:
        ...


class ContextManager:
    """Manage the context for one Stage execution.

    The raw ContextMessage history is kept for debug/checkpoint use. The
    ContextWindow view is the only LLM-facing view and repairs incomplete tool
    call/tool result pairs before a request is built.

    Pass a JsonConfig to enable automatic token estimation and truncation via
    prepare_context(). Without a config the manager still works but
    prepare_context() falls back to get_context_window() with no trimming.
    """

    def __init__(
        self,
        config: JsonConfig | None = None,
        strategy_name: str = "react",
    ) -> None:
        self._system_prompt = ""
        self._tool_schemas = ""
        self._messages: list[ContextMessage] = []
        self._variables: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._config = config
        self._strategy_name = strategy_name
        # Lazily-created caches: estimator per provider, truncator per strategy
        self._estimators: dict[str, BaseTokenEstimator] = {}
        self._truncator: _ContextTruncator | None = None

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def get_system_prompt(self) -> str:
        with self._lock:
            return self._system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        with self._lock:
            self._system_prompt = prompt

    def append_system_prompt(self, text: str) -> None:
        with self._lock:
            self._system_prompt += text

    def append_system_prompt_line(self, text: str) -> None:
        with self._lock:
            self._system_prompt += f"\n{text}"

    # ------------------------------------------------------------------
    # Message management
    # ------------------------------------------------------------------

    def add_message(
        self,
        role: LLMRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            message = ContextMessage(
                id=str(uuid4()),
                role=role,
                content=content,
                metadata=dict(metadata or {}),
            )
            self._messages.append(message)
            return message.id

    def update_message(self, message_id: str, content: str) -> None:
        with self._lock:
            self._messages = [
                self._replace_content(message, content) if message.id == message_id else message
                for message in self._messages
            ]

    def delete_message(self, message_id: str) -> None:
        with self._lock:
            self._messages = [message for message in self._messages if message.id != message_id]

    def get_message_by_id(self, message_id: str) -> ContextMessage | None:
        with self._lock:
            for message in self._history:
                if message.id == message_id:
                    return self._clone_context_message(message)
            return None

    def get_history(self, limit: int | None = None, offset: int = 0) -> list[ContextMessage]:
        with self._lock:
            messages = self._messages[offset:]
            if limit is not None:
                messages = messages[:limit]
            return [self._clone_context_message(message) for message in messages]

    def filter_by_role(self, role: LLMRole) -> list[ContextMessage]:
        with self._lock:
            return [
                self._clone_context_message(message)
                for message in self._messages
                if message.role == role
            ]

    def reset(self) -> None:
        with self._lock:
            self._messages.clear()
            self._variables.clear()

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    def set_variables(self, variables: dict[str, Any]) -> None:
        with self._lock:
            self._variables = dict(variables)

    def get_variables(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._variables)

    # ------------------------------------------------------------------
    # Token and LLM views
    # ------------------------------------------------------------------

    def get_token_count(self) -> int:
        with self._lock:
            total_chars = len(self._system_prompt)
            for msg in self._messages:
                total_chars += len(msg.content)
            return total_chars // 4  # rough 4-chars-per-token fallback

    def summarize(self, strategy: Any) -> None:
        with self._lock:
            replacement = strategy.summarize(self._to_llm_messages(self._messages))
            if isinstance(replacement, str):
                self._messages = [
                    ContextMessage(id=str(uuid4()), role="assistant", content=replacement)
                ]
            elif isinstance(replacement, list):
                self._messages = [self._from_llm_message(message) for message in replacement]
            self._history = list(self._messages)

    def get_conversation_history(self) -> list[LLMMessage]:
        with self._lock:
            return self._to_llm_messages(self._messages)

    def replace_conversation_history(self, messages: list[LLMMessage]) -> None:
        with self._lock:
            self._messages = [self._from_llm_message(m) for m in messages]

    def get_context_window(self) -> ContextWindow:
        with self._lock:
            messages = self._repair_tool_pairs(self._messages)
            llm_messages = self._to_llm_messages(messages)
            system_prompt = self._system_prompt_with_variables()
            rough_tokens = (len(system_prompt) + sum(len(m.content) for m in messages)) // 4
            return ContextWindow(
                system_prompt=system_prompt,
                messages=llm_messages,
                token_count=rough_tokens,
            )

    def prepare_context(self, provider_name: str) -> ContextWindow:
        """Return a context window ready for LLM consumption.

        Reads context_window size from config for the given provider, estimates
        current token usage, and truncates if usage exceeds the trim threshold.
        Falls back to get_context_window() when config is absent or the provider
        has no context_window entry.
        """
        window = self.get_context_window()
        if self._config is None:
            return window

        context_window_size: int = self._config.get(
            f"llm.provider_settings.{provider_name}.context_window", 0
        )
        if context_window_size <= 0:
            return window

        estimator = self._get_estimator(provider_name)
        request = LLMRequest(
            system_prompt=window.system_prompt,
            messages=window.messages,
        )
        estimation = estimator.estimate(request)
        if estimation["total"] <= context_window_size * 0.85:
            return window

        truncator = self._get_truncator()
        if truncator is None:
            return window

        result = truncator.truncate(request, context_window_size, estimator)
        if result.compacted_messages is None:
            return window

        compacted = result.compacted_messages
        est_after = estimator.estimate(
            LLMRequest(system_prompt=window.system_prompt, messages=compacted)
        )
        return ContextWindow(
            system_prompt=window.system_prompt,
            messages=compacted,
            token_count=est_after["total"],
        )

    def get_context(self) -> AgentContext:
        with self._lock:
            return AgentContext(
                system_prompt=self._system_prompt_with_variables(),
                messages=[self._clone_context_message(message) for message in self._history],
                variables=dict(self._variables),
                token_count=self.get_token_count(),
            )

    def release(self) -> None:
        with self._lock:
            self._system_prompt = ""
            self._messages.clear()
            self._history.clear()
            self._archived_tasks.clear()
            self._variables.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_estimator(self, provider_name: str) -> BaseTokenEstimator:
        if provider_name not in self._estimators:
            from agent.models.context.estimator.token_estimator import TokenEstimatorFactory
            self._estimators[provider_name] = TokenEstimatorFactory.get_estimator(provider_name)
        return self._estimators[provider_name]

    def _get_truncator(self) -> _ContextTruncator | None:
        if self._config is None or self._llm_client_factory is None:
            return None
        if self._truncator is None:
            from agent.models.context.truncation.token_truncation import TruncatorFactory
            from agent.models.context.budget.token_budget_manager import TokenBudgetManagerFactory
            from utils.log.log import get_logger
            budget_manager = TokenBudgetManagerFactory.create(self._strategy_name, self._config)
            logger = get_logger(__name__)
            self._truncator = TruncatorFactory.create(
                self._strategy_name, budget_manager, self._llm_client_factory, logger, self._config
            )
        return self._truncator

    def _system_prompt_with_variables(self) -> str:
        if not self._variables:
            return self._system_prompt
        lines = [self._system_prompt, "", "Task variables:"]
        lines.extend(f"- {key}: {value}" for key, value in sorted(self._variables.items()))
        return "\n".join(line for line in lines if line != "")

    @classmethod
    def _repair_tool_pairs(cls, messages: list[ContextMessage]) -> list[ContextMessage]:
        repaired = list(messages)
        while repaired:
            last = repaired[-1]
            if last.role != "assistant" or not last.metadata.get("tool_calls"):
                break
            tool_call_ids = {
                tool_call.get("llm_raw_tool_call_id")
                for tool_call in last.metadata.get("tool_calls", [])
                if isinstance(tool_call, dict)
            }
            following_tool_ids = {
                message.metadata.get("llm_raw_tool_call_id")
                for message in repaired
                if message.role == "tool"
            }
            if tool_call_ids and not tool_call_ids.issubset(following_tool_ids):
                repaired.pop()
                continue
            break
        return repaired

    @classmethod
    def _to_llm_messages(cls, messages: list[ContextMessage]) -> list[LLMMessage]:
        return [
            LLMMessage(
                role=message.role,
                content=message.content,
                metadata=dict(message.metadata),
            )
            for message in messages
        ]

    @staticmethod
    def _from_llm_message(message: LLMMessage) -> ContextMessage:
        return ContextMessage(
            id=str(uuid4()),
            role=message.role,
            content=message.content,
            metadata=dict(message.metadata),
        )

    @staticmethod
    def _clone_context_message(message: ContextMessage) -> ContextMessage:
        return ContextMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            metadata=dict(message.metadata),
            created_at=message.created_at,
        )

    @staticmethod
    def _replace_content(message: ContextMessage, content: str) -> ContextMessage:
        return ContextMessage(
            id=message.id,
            role=message.role,
            content=content,
            metadata=dict(message.metadata),
            created_at=message.created_at,
        )
