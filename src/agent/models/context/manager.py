from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from schemas import LLMMessage, UnifiedLLMRequest, LLMResponse
from schemas.task import KnowledgeEntry, Plan, Task, UserPreferenceEntry
from schemas.types import LLMRole

if TYPE_CHECKING:
    from agent.models.context.estimator.token_estimator import BaseTokenEstimator
    from agent.models.context.truncation.token_truncation import ContextTruncator
    from config.config import JsonConfig


@dataclass(frozen=True)
class ContextMessage:
    id: str
    role: LLMRole
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class StageRecord:
    stage_index: int
    first_message_id: str | None = None
    last_message_id: str | None = None
    summary: str | None = None
    dropped: bool = False


class ContextManager:
    """Single source of truth for all context sent to the LLM.

    Responsibilities:
    - Owns system_prompt, tool_schemas, knowledge_entries, user_preferences, variables
    - Tracks conversation messages in _ctx_window (mutable) and _history (append-only)
    - Tracks stage boundaries by message ID (immune to index shifting)
    - Assembles and optionally truncates LLMRequest via get_context_window()
    """

    def __init__(
        self,
        task: Task,
        plan: Plan,
        config: JsonConfig | None = None,
    ) -> None:
        self._config = config
        self._task = task
        self._plan = plan
        self._strategy_name = None #从配置里自己取

        self._system_prompt: str = ""
        self._tool_schemas: list[dict[str, Any]] = []
        self._knowledge_entries: list[KnowledgeEntry] = []
        self._user_preferences_entries: list[UserPreferenceEntry] = []
        self._variables: dict[str, Any] = {} #动态加入的参数

        self._ctx_window: list[ContextMessage] = []
        self._history: list[ContextMessage] = []

        self._stage_records: list[StageRecord] = []
        self._message_id_to_stage: dict[str, int] = {}
        self._active_stage_index: int | None = None
        self._last_success_stage_index: int | None = None
        
        self._token_estimator: BaseTokenEstimator = None 
        self._token_truncator: ContextTruncator | None = None

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Configuration setters
    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str) -> None:
        with self._lock:
            self._system_prompt = prompt

    def get_system_prompt(self) -> str:
        with self._lock:
            return self._system_prompt

    def append_system_prompt(self, text: str) -> None:
        with self._lock:
            self._system_prompt += text

    def append_system_prompt_line(self, text: str) -> None:
        with self._lock:
            self._system_prompt += f"\n{text}"

    def set_tool_schemas(self, schemas: list[dict[str, Any]]) -> None:
        with self._lock:
            self._tool_schemas = list(schemas)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._tool_schemas)

    def set_knowledge_entries(self, entries: list[KnowledgeEntry]) -> None:
        with self._lock:
            self._knowledge_entries = list(entries)

    def set_user_preferences(self, entries: list[UserPreferenceEntry]) -> None:
        with self._lock:
            self._user_preferences_entries = list(entries)

    def set_variables(self, variables: dict[str, Any]) -> None:
        with self._lock:
            self._variables = dict(variables)

    def get_variables(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._variables)

    # ------------------------------------------------------------------
    # Stage lifecycle
    # ------------------------------------------------------------------

    def begin_stage(self, stage_index: int) -> None:
        """Record the start of a new stage. The next add_message call will
        set first_message_id for this stage."""
        with self._lock:
            while len(self._stage_records) <= stage_index:
                self._stage_records.append(
                    StageRecord(stage_index=len(self._stage_records))
                )
            self._active_stage_index = stage_index

    def end_stage(self, stage_index: int, success: bool) -> None:
        """Mark the stage as complete, recording last_message_id."""
        with self._lock:
            if stage_index >= len(self._stage_records):
                return
            record = self._stage_records[stage_index]
            last_id = self._ctx_window[-1].id if self._ctx_window else None
            self._stage_records[stage_index] = StageRecord(
                stage_index=record.stage_index,
                first_message_id=record.first_message_id,
                last_message_id=last_id,
                summary=record.summary,
                dropped=record.dropped,
            )
            if self._active_stage_index == stage_index:
                self._active_stage_index = None
            if success:
                self._last_success_stage_index = stage_index

    def drop_stage(self, stage_index: int) -> None:
        """Remove all ctx_window messages for stage_index. History is unchanged."""
        with self._lock:
            if stage_index >= len(self._stage_records):
                return
            stage_msg_ids = self._get_stage_message_ids(stage_index)
            self._ctx_window = [m for m in self._ctx_window if m.id not in stage_msg_ids]
            record = self._stage_records[stage_index]
            self._stage_records[stage_index] = StageRecord(
                stage_index=record.stage_index,
                first_message_id=record.first_message_id,
                last_message_id=record.last_message_id,
                summary=record.summary,
                dropped=True,
            )

    def summarize_stage(self, stage_index: int, summary: str) -> None:
        """Replace stage messages in ctx_window with a single summary message."""
        with self._lock:
            if stage_index >= len(self._stage_records):
                return
            stage_msg_ids = self._get_stage_message_ids(stage_index)
            if not stage_msg_ids:
                return

            summary_msg = ContextMessage(
                id=str(uuid4()),
                role="assistant",
                content=summary,
                metadata={"summarized": True, "stage_index": stage_index},
            )
            new_window: list[ContextMessage] = []
            inserted = False
            for m in self._ctx_window:
                if m.id in stage_msg_ids:
                    if not inserted:
                        new_window.append(summary_msg)
                        self._message_id_to_stage[summary_msg.id] = stage_index
                        inserted = True
                else:
                    new_window.append(m)
            self._ctx_window = new_window

            record = self._stage_records[stage_index]
            self._stage_records[stage_index] = StageRecord(
                stage_index=record.stage_index,
                first_message_id=record.first_message_id,
                last_message_id=record.last_message_id,
                summary=summary,
                dropped=record.dropped,
            )

    def get_stage_messages(self, stage_index: int) -> list[LLMMessage]:
        """Return ctx_window messages for stage_index as LLMMessages."""
        with self._lock:
            if stage_index >= len(self._stage_records):
                return []
            if self._stage_records[stage_index].dropped:
                return []
            stage_msg_ids = self._get_stage_message_ids(stage_index)
            msgs = [m for m in self._ctx_window if m.id in stage_msg_ids]
            return self._to_llm_messages(msgs)

    # ------------------------------------------------------------------
    # Message management
    # ------------------------------------------------------------------

    def add_message(
        self,
        role: LLMRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append a message to ctx_window and history. Returns the message UUID."""
        with self._lock:
            msg = ContextMessage(
                id=str(uuid4()),
                role=role,
                content=content,
                metadata=dict(metadata) if metadata else {},
            )
            self._ctx_window.append(msg)
            self._history.append(msg)

            if self._active_stage_index is not None:
                idx = self._active_stage_index
                self._message_id_to_stage[msg.id] = idx
                record = self._stage_records[idx]
                if record.first_message_id is None:
                    self._stage_records[idx] = StageRecord(
                        stage_index=record.stage_index,
                        first_message_id=msg.id,
                        last_message_id=record.last_message_id,
                        summary=record.summary,
                        dropped=record.dropped,
                    )
            return msg.id

    def add_llm_response(self, response: LLMResponse) -> None:
        """Append the assistant message from an LLMResponse to context."""
        msg = response.assistant_message
        self.add_message(
            role=msg.role,
            content=msg.content,
            metadata=dict(msg.metadata),
        )

    def get_conversation_history(self) -> list[LLMMessage]:
        """Return the full append-only history as LLMMessages."""
        with self._lock:
            return self._to_llm_messages(list(self._history))

    def replace_conversation_history(self, messages: list[LLMMessage]) -> None:
        """Replace ctx_window and history (used for checkpoint restore)."""
        with self._lock:
            ctx_msgs = [self._from_llm_message(m) for m in messages]
            self._ctx_window = ctx_msgs
            self._history = list(ctx_msgs)
            self._stage_records = []
            self._message_id_to_stage = {}
            self._active_stage_index = None

    # ------------------------------------------------------------------
    # Core: build LLMRequest
    # ------------------------------------------------------------------

    def get_context_window(self, provider_name: str) -> UnifiedLLMRequest:
        """Assemble, optionally truncate, and return the LLMRequest for the LLM."""
        with self._lock:
            system_prompt = self._build_system_prompt()
            repaired = self._repair_tool_pairs(list(self._ctx_window))
            messages = self._to_llm_messages(repaired)
            request = UnifiedLLMRequest(
                system_prompt=system_prompt,
                messages=messages,
                tool_schemas=self._tool_schemas if self._tool_schemas else None,
            )
            truncator = self._get_truncator()
            if truncator is not None:
                estimator = self._get_estimator(provider_name)
                total_budget = self._get_total_budget(provider_name)
                result = truncator.truncate(request, total_budget, estimator)
                return result.request
            return request

    # ------------------------------------------------------------------
    # Reset / release
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear ctx_window and stage tracking. Preserves history and config."""
        with self._lock:
            self._ctx_window.clear()
            self._stage_records.clear()
            self._message_id_to_stage.clear()
            self._active_stage_index = None

    def release(self) -> None:
        """Full teardown: clear everything."""
        with self._lock:
            self._system_prompt = ""
            self._tool_schemas = []
            self._knowledge_entries = []
            self._user_preferences_entries = []
            self._variables = {}
            self._ctx_window.clear()
            self._history.clear()
            self._stage_records.clear()
            self._message_id_to_stage.clear()
            self._active_stage_index = None
            self._token_estimator = None
            self._token_truncator = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from base + knowledge + preferences + variables."""
        parts: list[str] = [self._system_prompt]

        if self._knowledge_entries:
            lines = ["## Knowledge Base"]
            for entry in self._knowledge_entries:
                lines.append(f"- {entry.title}: {entry.content}")
            parts.append("\n".join(lines))

        if self._user_preferences_entries:
            lines = ["## User Preferences"]
            for pref in self._user_preferences_entries:
                lines.append(f"- {pref.content}")
            parts.append("\n".join(lines))

        if self._variables:
            lines = ["## Task Variables"]
            for key, value in sorted(self._variables.items()):
                lines.append(f"- {key}: {value}")
            parts.append("\n".join(lines))

        return "\n\n".join(p for p in parts if p)

    def _get_stage_message_ids(self, stage_index: int) -> set[str]:
        """Return the set of ctx_window message IDs belonging to stage_index."""
        return {
            msg_id
            for msg_id, idx in self._message_id_to_stage.items()
            if idx == stage_index
        }

    def _get_estimator(self, provider_name: str) -> BaseTokenEstimator:
        if self._token_estimator is not None:
            return self._token_estimator

        from agent.models.context.estimator.token_estimator import TokenEstimatorFactory
        self._token_estimator = TokenEstimatorFactory.get_estimator(provider_name)
        return self._token_estimator

    def _get_truncator(self) -> ContextTruncator | None:
        if self._token_truncator is not None:
            return self._token_truncator
        if self._config is None:
            return None
        from agent.models.context.budget.token_budget_manager import TokenBudgetManagerFactory
        from agent.models.context.truncation.token_truncation import TruncatorFactory
        from utils.log.log import get_logger
        budget_manager = TokenBudgetManagerFactory.create(self._strategy_name, self._config)
        logger = get_logger(__name__)
        self._token_truncator = TruncatorFactory.create(
            self._strategy_name,
            budget_manager,
            logger,
            self._config,
        )
        return self._token_truncator

    def _get_total_budget(self, provider_name: str) -> int:
        if self._config is None:
            return 32000
        return int(
            self._config.get(
                f"llm.provider_settings.{provider_name}.context_window", 32000
            )
        )

    @classmethod
    def _repair_tool_pairs(cls, messages: list[ContextMessage]) -> list[ContextMessage]:
        """Remove trailing assistant messages whose tool calls have no matching tool results."""
        repaired = list(messages)
        while repaired:
            last = repaired[-1]
            if last.role != "assistant" or not last.metadata.get("tool_calls"):
                break
            tool_call_ids = {
                tc.get("llm_raw_tool_call_id")
                for tc in last.metadata.get("tool_calls", [])
                if isinstance(tc, dict)
            }
            following_tool_ids = {
                m.metadata.get("llm_raw_tool_call_id")
                for m in repaired
                if m.role == "tool"
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
