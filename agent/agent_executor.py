from __future__ import annotations

from typing import TYPE_CHECKING

from context.agent_context import AgentContext
from context.session import Session
from schemas import (
    AGENT_STRATEGY_NOT_FOUND,
    AgentExecutionResult,
    ChatMessage,
    build_error,
)
from strategy.impl import ReActStrategy
from tools import (
    SQLQueryTool,
    SQLSchemaTool,
    VectorSearchTool,
    VectorSchemaTool,
    build_sql_query_tool_description,
    build_sql_query_tool_name,
    build_sql_schema_tool_description,
    build_sql_schema_tool_name,
    build_vector_search_tool_description,
    build_vector_search_tool_name,
    build_vector_schema_tool_description,
    build_vector_schema_tool_name,
)

if TYPE_CHECKING:
    from config import JsonConfig
    from storage import StorageRegistry
    from strategy.strategy import Strategy
    from tools import ToolRegistry
    from tracing import Tracer
    from utils.log import Logger


class AgentExecutor:
    def __init__(
        self,
        session: Session,
        tool_registry: ToolRegistry,
        storage_registry: StorageRegistry | None,
        config: JsonConfig,
        tracer: Tracer | None,
        logger: Logger,
    ) -> None:
        self._session = session
        self._tool_registry = tool_registry
        self._agent_context = AgentContext()
        self._cur_iterations = 0

        self._strategy = self._build_strategy(config, tracer)
        self._register_storage_tools(storage_registry)
        self._base_system_prompt = self._agent_context.get_system_prompt()

    # ------------------------------------------------------------------
    # Conversation interfaces (for Strategy use)
    # ------------------------------------------------------------------

    def append_conversation(self, message: ChatMessage) -> None:
        self._agent_context.append_conversation_message(message)

    def get_conversation(self) -> list[ChatMessage]:
        return self._agent_context.get_conversation_history()

    def get_trimmed_conversation(self, max_messages: int | None) -> list[ChatMessage]:
        conversation = self._agent_context.get_conversation_history()
        if max_messages is None or max_messages <= 0 or len(conversation) <= max_messages:
            return conversation
        # Drop complete ReAct units from the front until within the limit.
        # A unit is: one user message, or one assistant message + all immediately
        # following tool messages. This guarantees we never split an
        # assistant/tool-call group, which OpenAI and Claude APIs reject.
        result = list(conversation)
        while len(result) > max_messages:
            if not result:
                break
            end = 1
            if result[0].role == "assistant":
                while end < len(result) and result[end].role == "tool":
                    end += 1
            del result[:end]
        return result

    def get_system_prompt(self) -> str:
        return self._agent_context.get_system_prompt()

    def set_system_prompt(self, prompt: str) -> None:
        self._agent_context.set_system_prompt(prompt)

    def append_system_prompt(self, text: str) -> None:
        self._agent_context.append_system_prompt(text)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def begin_session(self) -> None:
        self._cur_iterations = 0
        self._session.begin()

    def reset(self, archive_current_task: bool = False) -> None:
        self._cur_iterations = 0
        if archive_current_task:
            self._agent_context.archive_current_task()
        else:
            self._agent_context.clear_current_task()
        self._session.reset()
        self._restore_base_system_prompt()

    def release_resources(self) -> None:
        self.reset(archive_current_task=False)
        self._agent_context.release()

    def get_iterations(self) -> int:
        return self._cur_iterations

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
        self._cur_iterations += 1
        return self._strategy.execute(self, self._tool_registry, user_message)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_strategy(self, config: JsonConfig, tracer: Tracer | None) -> Strategy:
        strategy_name = str(config.get("agent.strategy", "react")).strip()
        if strategy_name == "react":
            strategy = ReActStrategy(config, tracer)
        else:
            raise build_error(
                AGENT_STRATEGY_NOT_FOUND,
                f"Unsupported agent strategy: {strategy_name}",
            )
        strategy.init_context(self)
        return strategy

    def _restore_base_system_prompt(self) -> None:
        if self._agent_context.get_system_prompt() == self._base_system_prompt:
            return
        self._agent_context.release()
        self._agent_context = AgentContext()
        self._agent_context.set_system_prompt(self._base_system_prompt)

    def _register_storage_tools(self, storage_registry: StorageRegistry | None) -> None:
        if storage_registry is None:
            return

        sql_tool_lines: list[str] = []
        vector_tool_lines: list[str] = []

        for backend_name in storage_registry.list_backends():
            storage = storage_registry.get(backend_name)
            if backend_name in {"sqlite", "mysql"}:
                schema_tool_name = build_sql_schema_tool_name(backend_name)
                schema_description = build_sql_schema_tool_description(backend_name)
                self._tool_registry.register(
                    SQLSchemaTool(
                        name=schema_tool_name,
                        description=schema_description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                tool_name = build_sql_query_tool_name(backend_name)
                description = build_sql_query_tool_description(backend_name)
                self._tool_registry.register(
                    SQLQueryTool(
                        name=tool_name,
                        description=description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                resources = ", ".join(storage.list_resources()) or "<none>"
                sql_tool_lines.append(
                    f"- `{schema_tool_name}`: {schema_description} Available databases: {resources}"
                )
                sql_tool_lines.append(
                    f"- `{tool_name}`: {description} Available databases: {resources}"
                )
                continue

            if backend_name == "chromadb":
                schema_tool_name = build_vector_schema_tool_name(backend_name)
                schema_description = build_vector_schema_tool_description(backend_name)
                self._tool_registry.register(
                    VectorSchemaTool(
                        name=schema_tool_name,
                        description=schema_description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                tool_name = build_vector_search_tool_name(backend_name)
                description = build_vector_search_tool_description(backend_name)
                self._tool_registry.register(
                    VectorSearchTool(
                        name=tool_name,
                        description=description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                resources = ", ".join(storage.list_resources()) or "<none>"
                vector_tool_lines.append(
                    f"- `{schema_tool_name}`: {schema_description} Available collections: {resources}"
                )
                vector_tool_lines.append(
                    f"- `{tool_name}`: {description} Available collections: {resources}"
                )

        if sql_tool_lines:
            self._agent_context.append_system_prompt(
                "\n\nRelational query tool guide:\n"
                "Use relational query tools for SQLite or MySQL tables with custom schemas.\n"
                "Choose the correct authorized database for the task.\n"
                "When you are unsure about available tables or columns, use the dedicated schema inspection tool first.\n"
                "For SQL query tools, send only a single SELECT statement and keep values in params instead of string interpolation.\n"
                f"{chr(10).join(sql_tool_lines)}"
            )

        if vector_tool_lines:
            self._agent_context.append_system_prompt(
                "\n\nVector search tool guide:\n"
                "Use vector search tools for semantic retrieval from indexed text collections.\n"
                "When you are unsure which collection to use, inspect the available collections first.\n"
                "Choose the most relevant authorized collection for the task.\n"
                "Prefer them when the task needs fuzzy matching, semantic lookup, or concept-level retrieval.\n"
                f"{chr(10).join(vector_tool_lines)}"
            )
