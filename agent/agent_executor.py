from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config import ConfigValueReader
from context.agent_context import AgentContext
from schemas import (
    AGENT_STRATEGY_NOT_FOUND,
    AgentExecutionResult,
    ChatMessage,
    STORAGE_CONFIG_ERROR,
    build_error,
)
from storage import ChromaDBStorage, MySQLStorage, SQLiteStorage, StorageRegistry
from storage.bootstrap_documents import load_seed_documents
from strategy.impl import ReActStrategy
from tools import (
    SQLQueryTool,
    SQLSchemaTool,
    ToolRegistry,
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
    create_default_tool_registry,
)

if TYPE_CHECKING:
    from config import JsonConfig
    from strategy.strategy import Strategy
    from tracing import Tracer
    from utils.log import Logger


class AgentExecutor:
    def __init__(
        self,
        config: JsonConfig,
        tracer: Tracer | None,
        logger: Logger,
    ) -> None:
        self._agent_context = AgentContext()

        config_reader = ConfigValueReader(config)
        self._storage_registry = self._build_storage_registry(config)
        self._tool_registry = self._build_tool_registry(config, config_reader, tracer, logger)
        self._strategy = self._build_strategy(config, tracer)
        self._register_storage_tools(self._storage_registry)

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

    def reset(self, archive_current_task: bool = False) -> None:
        if archive_current_task:
            self._agent_context.archive_current_task()
        else:
            self._agent_context.clear_current_task()
        self._restore_base_system_prompt()

    def release_resources(self) -> None:
        self.reset(archive_current_task=False)
        self._agent_context.release()
        self._storage_registry.close_all()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
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
        self._agent_context = AgentContext()

    @staticmethod
    def _build_storage_registry(config: JsonConfig) -> StorageRegistry:
        seed_documents = load_seed_documents(
            config.get("storage.file.path", "runtime/nanoagent_soul.json")
        )
        sqlite_databases = AgentExecutor._build_sqlite_databases(config)
        storages = [SQLiteStorage(sqlite_databases)]

        chromadb_path = config.get("storage.chromadb.persist_directory")
        if chromadb_path:
            chromadb_collections = AgentExecutor._build_chromadb_collections(config)
            chromadb_storage = ChromaDBStorage(
                persist_directory=chromadb_path,
                collections=chromadb_collections,
            )
            bootstrap_collection = config.get("storage.chromadb.bootstrap_collection")
            if isinstance(bootstrap_collection, str) and bootstrap_collection.strip():
                if not chromadb_storage.get_documents(bootstrap_collection):
                    chromadb_storage.upsert_documents(bootstrap_collection, seed_documents)
            storages.append(chromadb_storage)

        mysql_host = str(config.get("storage.mysql.host", "")).strip()
        if mysql_host:
            storages.append(MySQLStorage(
                host=mysql_host,
                port=int(config.get("storage.mysql.port", 3306)),
                user=os.getenv("MYSQL_USER", ""),
                password=os.getenv("MYSQL_PASSWORD", ""),
                allowed_databases=AgentExecutor._build_mysql_databases(config),
                charset=str(config.get("storage.mysql.charset", "utf8mb4")),
            ))

        return StorageRegistry(storages)

    @staticmethod
    def _build_sqlite_databases(config: JsonConfig) -> dict[str, str]:
        sqlite_config = config.get("storage.sqlite", {})
        if not isinstance(sqlite_config, dict):
            sqlite_config = {}
        configured = sqlite_config.get("allowed_databases") or sqlite_config.get("databases")
        databases: dict[str, str] = {}
        if isinstance(configured, dict):
            for name, path in configured.items():
                if str(name).strip() and str(path).strip():
                    databases[str(name).strip()] = str(path).strip()
        fallback_path = str(sqlite_config.get("path", "")).strip()
        if fallback_path:
            databases.setdefault(AgentExecutor._derive_sqlite_alias(fallback_path), fallback_path)
        if not databases:
            databases["local_storage"] = "runtime/nanoagent_local_storage.db"
        return databases

    @staticmethod
    def _build_mysql_databases(config: JsonConfig) -> list[str]:
        mysql_config = config.get("storage.mysql", {})
        if not isinstance(mysql_config, dict):
            mysql_config = {}
        configured = mysql_config.get("allowed_databases")
        if isinstance(configured, list):
            databases = [str(d).strip() for d in configured if str(d).strip()]
            if databases:
                return databases
        fallback = str(mysql_config.get("database", "")).strip()
        if fallback:
            return [fallback]
        raise build_error(
            STORAGE_CONFIG_ERROR,
            "MySQL storage requires `storage.mysql.allowed_databases` or `storage.mysql.database`.",
        )

    @staticmethod
    def _build_chromadb_collections(config: JsonConfig) -> list[str]:
        chromadb_config = config.get("storage.chromadb", {})
        if not isinstance(chromadb_config, dict):
            chromadb_config = {}
        configured = chromadb_config.get("allowed_collections") or chromadb_config.get("collections")
        if isinstance(configured, list):
            collections = [str(c).strip() for c in configured if str(c).strip()]
            if collections:
                return collections
        fallback = str(chromadb_config.get("collection_name", "")).strip()
        if fallback:
            return [fallback]
        return ["agent_documents"]

    @staticmethod
    def _derive_sqlite_alias(path_value: str) -> str:
        path = str(path_value).strip()
        if path.endswith(".db"):
            path = path[:-3]
        return path.rsplit("/", 1)[-1] or "sqlite"

    @staticmethod
    def _build_tool_registry(
        config: JsonConfig,
        config_reader: ConfigValueReader,
        tracer: Tracer | None,
        logger: Logger,
    ) -> ToolRegistry:
        package_name = config.get("tools.package", "tools.impl")
        if not isinstance(package_name, str) or not package_name.strip():
            package_name = "tools.impl"
        module_names = config.get("tools.modules", [])
        if not isinstance(module_names, list):
            module_names = []
        return create_default_tool_registry(
            module_names=module_names,
            package_name=package_name,
            timeout_retry_max_attempts=int(config.get("tools.retry.max_attempts", 4)),
            timeout_retry_delays=config_reader.retry_delays("tools.retry.backoff_seconds"),
            tracer=tracer,
            logger=logger,
        )

    def _register_storage_tools(self, storage_registry: StorageRegistry) -> None:
        sql_tool_lines: list[str] = []
        vector_tool_lines: list[str] = []

        for backend_name in storage_registry.list_backends():
            storage = storage_registry.get(backend_name)
            if backend_name in {"sqlite", "mysql"}:
                schema_tool_name = build_sql_schema_tool_name(backend_name)
                schema_description = build_sql_schema_tool_description(backend_name)
                self._tool_registry.register(SQLSchemaTool(
                    name=schema_tool_name,
                    description=schema_description,
                    storage=storage,
                    backend_name=backend_name,
                ))
                tool_name = build_sql_query_tool_name(backend_name)
                description = build_sql_query_tool_description(backend_name)
                self._tool_registry.register(SQLQueryTool(
                    name=tool_name,
                    description=description,
                    storage=storage,
                    backend_name=backend_name,
                ))
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
                self._tool_registry.register(VectorSchemaTool(
                    name=schema_tool_name,
                    description=schema_description,
                    storage=storage,
                    backend_name=backend_name,
                ))
                tool_name = build_vector_search_tool_name(backend_name)
                description = build_vector_search_tool_description(backend_name)
                self._tool_registry.register(VectorSearchTool(
                    name=tool_name,
                    description=description,
                    storage=storage,
                    backend_name=backend_name,
                ))
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
