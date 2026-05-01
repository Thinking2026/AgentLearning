from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from schemas import LLMMessage, LLMRequest
from schemas.types import LLMRole


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
    token_count: int


@dataclass(frozen=True)
class FullContext:
    system_prompt: str
    messages: list[ContextMessage]
    variables: dict[str, Any]
    token_count: int


class ContextTruncator(Protocol):
    def truncate(self, request: LLMRequest, total_budget: int, estimator: Any) -> Any:
        ...


class ContextManager:
    """Manage the context for one Stage execution.

    The raw ContextMessage history is kept for debug/checkpoint use. The
    ContextWindow view is the only LLM-facing view and repairs incomplete tool
    call/tool result pairs before a request is built.
    """

    def __init__(self) -> None:
        self._system_prompt = ""
        self._messages: list[ContextMessage] = []
        self._history: list[ContextMessage] = []
        self._archived_tasks: list[list[ContextMessage]] = []
        self._variables: dict[str, Any] = {}
        self._lock = threading.RLock()

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
            self._history.append(message)
            return message.id

    def update_message(self, message_id: str, content: str) -> None:
        with self._lock:
            self._messages = [
                self._replace_content(message, content) if message.id == message_id else message
                for message in self._messages
            ]
            self._history = [
                self._replace_content(message, content) if message.id == message_id else message
                for message in self._history
            ]

    def delete_message(self, message_id: str) -> None:
        with self._lock:
            self._messages = [message for message in self._messages if message.id != message_id]
            self._history = [message for message in self._history if message.id != message_id]

    def get_message_by_id(self, message_id: str) -> ContextMessage | None:
        with self._lock:
            for message in self._history:
                if message.id == message_id:
                    return self._clone_context_message(message)
            return None

    def get_history(self, limit: int | None = None, offset: int = 0) -> list[ContextMessage]:
        with self._lock:
            messages = self._history[offset:]
            if limit is not None:
                messages = messages[:limit]
            return [self._clone_context_message(message) for message in messages]

    def filter_by_role(self, role: LLMRole) -> list[ContextMessage]:
        with self._lock:
            return [
                self._clone_context_message(message)
                for message in self._history
                if message.role == role
            ]

    def reset(self) -> None:
        with self._lock:
            self._messages.clear()
            self._history.clear()
            self._archived_tasks.clear()
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
            return self._estimate_tokens(self._system_prompt, self._messages, self._variables)

    def trim_to_max_tokens(self, max_tokens: int, truncator: ContextTruncator, estimator: Any = None) -> None:
        with self._lock:
            request = LLMRequest(
                system_prompt=self._system_prompt,
                messages=self._to_llm_messages(self._messages),
            )
            result = truncator.truncate(request, max_tokens, estimator)
            trimmed_request = getattr(result, "request", result)
            self._messages = [
                self._from_llm_message(message)
                for message in getattr(trimmed_request, "messages", [])
            ]
            self._history = list(self._messages)

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

    def get_context_window(self) -> ContextWindow:
        with self._lock:
            messages = self._repair_tool_pairs(self._messages)
            llm_messages = self._to_llm_messages(messages)
            return ContextWindow(
                system_prompt=self._system_prompt_with_variables(),
                messages=llm_messages,
                token_count=self._estimate_tokens(self._system_prompt, messages, self._variables),
            )

    def get_context(self) -> FullContext:
        with self._lock:
            return FullContext(
                system_prompt=self._system_prompt_with_variables(),
                messages=[self._clone_context_message(message) for message in self._history],
                variables=dict(self._variables),
                token_count=self.get_token_count(),
            )

    # ------------------------------------------------------------------
    # Compatibility adapters for existing callers/tests
    # ------------------------------------------------------------------

    def append_conversation_message(self, message: LLMMessage) -> None:
        self.add_message(message.role, message.content, message.metadata)

    def get_conversation_history(self) -> list[LLMMessage]:
        with self._lock:
            messages: list[LLMMessage] = []
            for task_messages in self._archived_tasks:
                messages.extend(self._to_llm_messages(task_messages))
            messages.extend(self.get_context_window().messages)
            return messages

    def clear_conversation_history(self) -> None:
        self.reset()

    def archive_current_task(self) -> None:
        with self._lock:
            if not self._messages:
                return
            self._archived_tasks.append([
                self._clone_context_message(message)
                for message in self._messages
            ])
            self._messages.clear()

    def clear_current_task(self) -> None:
        with self._lock:
            self._messages.clear()

    def replace_conversation_history(self, messages: list[LLMMessage]) -> None:
        with self._lock:
            self._messages = [self._from_llm_message(message) for message in messages]
            self._history = list(self._messages)
            self._archived_tasks.clear()

    def get_archived_tasks(self) -> list[list[LLMMessage]]:
        with self._lock:
            return [self._to_llm_messages(task_messages) for task_messages in self._archived_tasks]

    def get_current_task_messages(self) -> list[LLMMessage]:
        with self._lock:
            return self._to_llm_messages(self._messages)

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

    @staticmethod
    def _estimate_tokens(
        system_prompt: str,
        messages: list[ContextMessage],
        variables: dict[str, Any],
    ) -> int:
        chars = len(system_prompt)
        chars += sum(len(message.content) for message in messages)
        chars += sum(len(str(key)) + len(str(value)) for key, value in variables.items())
        return max(1, chars // 4) if chars else 0

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
