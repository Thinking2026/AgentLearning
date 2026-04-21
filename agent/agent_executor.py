from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config import ConfigValueReader
from context.agent_context import AgentContext
from schemas import (
    AGENT_EXECUTION_ERROR,
    AGENT_STRATEGY_NOT_FOUND,
    AgentError,
    AgentExecutionResult,
    ChatMessage,
    LLM_ALL_PROVIDERS_FAILED,
    LLM_PROVIDER_NOT_FOUND,
    STORAGE_CONFIG_ERROR,
    build_error,
)
from infra.db import ChromaDBStorage, MySQLStorage, SQLiteStorage, StorageRegistry
from infra.db.bootstrap_documents import load_seed_documents
from agent.strategy.decision import FinalAnswer, InvokeTools, ResponseTruncated
from agent.strategy.impl import ReActStrategy
from llm import (
    BaseLLMClient,
    ClaudeLLMClient,
    DeepSeekLLMClient,
    LLMProviderRegistry,
    OpenAILLMClient,
    QwenLLMClient,
    RetryConfig,
    SingleProviderClient,
)
from llm.routing import LLMProviderRouter
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
from utils.log import Logger, zap

if TYPE_CHECKING:
    from config import JsonConfig
    from agent.strategy.strategy import Strategy
    from runtime.tracing import Tracer


class AgentExecutor:
    def __init__(
        self,
        config: JsonConfig,
        tracer: Tracer | None,
        logger: Logger,
    ) -> None:
        self._logger = logger
        self._agent_context = AgentContext()

        config_reader = ConfigValueReader(config)
        self._storage_registry = self._build_storage_registry(config)
        self._tool_registry = self._build_tool_registry(config, config_reader, tracer, logger)
        self._llm_provider_router = self._build_llm_provider_router(config, tracer)
        self._strategy = self._build_strategy(config)
        self._register_storage_tools(self._storage_registry)

    # ------------------------------------------------------------------
    # Conversation interfaces (for Strategy use)
    # ------------------------------------------------------------------

    def append_conversation(self, message: ChatMessage) -> None:
        self._agent_context.append_conversation_message(message)

    def get_conversation(self) -> list[ChatMessage]:
        return self._agent_context.get_conversation_history()

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
        self._logger.info("AgentExecutor reset", zap.any("archive_current_task", archive_current_task))
        if archive_current_task:
            self._agent_context.archive_current_task()
        self._agent_context.clear_current_task()
        self._tool_registry.reset_all()

    def release_resources(self) -> None:
        self._logger.info("AgentExecutor releasing resources")
        self._agent_context.release()
        self._storage_registry.close_all()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
        self._logger.info(
            "AgentExecutor run start",
            zap.any("has_user_message", user_message is not None),
            zap.any("user_message", user_message.content[:200] if user_message else None),
        )

        if user_message is not None and user_message.content.strip():
            self.append_conversation(ChatMessage(role="user", content=user_message.content.strip()))

        result = self._run_loop()

        self._logger.info(
            "AgentExecutor run complete",
            zap.any("task_completed", result.task_completed),
            zap.any("has_error", result.error is not None),
            zap.any("error", str(result.error) if result.error else None),
        )
        return result

    def _run_loop(self) -> AgentExecutionResult:
        user_messages: list[ChatMessage] = []

        while True:
            request = self._strategy.build_llm_request(
                system_prompt=self.get_system_prompt(),
                conversation=self.get_conversation(),
                tool_schemas=self._tool_registry.get_tool_schemas(),
            )

            try:
                llm_response = self._llm_provider_router.route(request)
            except AgentError as exc:
                return AgentExecutionResult(
                    user_messages=user_messages,
                    error=exc if exc.code == LLM_ALL_PROVIDERS_FAILED else build_error(AGENT_EXECUTION_ERROR, str(exc)),
                )

            decision = self._strategy.parse_llm_response(llm_response)

            if isinstance(decision, ResponseTruncated):
                self.append_conversation(decision.message)
                user_messages.append(decision.message)
                return AgentExecutionResult(user_messages=user_messages, error=decision.error)

            if isinstance(decision, FinalAnswer):
                self.append_conversation(decision.message)
                user_messages.append(decision.message)
                return AgentExecutionResult(user_messages=user_messages, task_completed=True)

            # InvokeTools
            self.append_conversation(decision.assistant_message)
            llm_content = decision.assistant_message.content.strip()
            if llm_content:
                user_messages.append(ChatMessage(
                    role="assistant",
                    content=llm_content,
                    metadata={"source": "llm"},
                ))

            self._logger.info(
                "Tool calls dispatched",
                zap.any("tools", [tc.name for tc in decision.tool_calls]),
            )
            for tool_call in decision.tool_calls:
                result = self._tool_registry.execute(
                    tool_call.name,
                    tool_call.arguments,
                    tool_call.llm_raw_tool_call_id,
                )
                if not result.success:
                    self._logger.error(
                        "Tool call failed",
                        zap.any("tool", tool_call.name),
                        zap.any("error_code", result.error.code if result.error else None),
                        zap.any("error", result.error.message if result.error else None),
                    )
                observation = self._strategy.format_tool_observation(
                    tool_name=tool_call.name,
                    output=result.output,
                    llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                )
                self.append_conversation(observation)
                user_messages.append(ChatMessage(
                    role="assistant",
                    content=f"[tool:{tool_call.name}] {result.output}",
                    metadata={
                        "source": "tool",
                        "tool_name": tool_call.name,
                        "tool_arguments": tool_call.arguments,
                        "tool_result": result.output,
                        "tool_success": result.success,
                    },
                ))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_strategy(self, config: JsonConfig) -> Strategy:
        strategy_name = str(config.get("agent.strategy", "react")).strip()
        if strategy_name == "react":
            strategy = ReActStrategy()
        else:
            raise build_error(
                AGENT_STRATEGY_NOT_FOUND,
                f"Unsupported agent strategy: {strategy_name}",
            )
        strategy.init_context(self)
        return strategy

    @staticmethod
    def _build_llm_provider_router(config: JsonConfig, tracer: Tracer | None) -> LLMProviderRouter:
        priority_chain = config.get("llm.priority_chain", ["deepseek"])
        if not isinstance(priority_chain, list) or not priority_chain:
            priority_chain = ["deepseek"]

        registry = LLMProviderRegistry()
        for name in priority_chain:
            registry.register(AgentExecutor._build_provider(name, config, tracer))

        retry_config = RetryConfig(
            retry_base=float(config.get("llm.retry.base", 0.5)),
            retry_max_delay=float(config.get("llm.retry.max_delay", 60.0)),
            retry_max_attempts=int(config.get("llm.retry.max_attempts", 5)),
        )
        return LLMProviderRouter(
            registry=registry,
            priority_chain=priority_chain,
            retry_config=retry_config,
            enable_fallback=bool(config.get("llm.enable_provider_fallback", False)),
        )

    @staticmethod
    def _build_provider(provider_name: str, config: JsonConfig, tracer: Tracer | None) -> BaseLLMClient:
        provider_settings = config.get(f"llm.provider_settings.{provider_name}", {})
        if not isinstance(provider_settings, dict):
            provider_settings = {}
        timeout = float(provider_settings.get("timeout", config.get("llm.timeout", 60.0)))
        api_key = provider_settings.get("api_key")

        if provider_name == "openai":
            return OpenAILLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "gpt-4o-mini"),
                base_url=provider_settings.get("base_url", "https://api.openai.com/v1"),
                timeout=timeout,
            ).set_tracer(tracer)
        if provider_name == "qwen":
            return QwenLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "qwen-plus"),
                base_url=provider_settings.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout=timeout,
            ).set_tracer(tracer)
        if provider_name == "deepseek":
            return DeepSeekLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "deepseek-chat"),
                base_url=provider_settings.get("base_url", "https://api.deepseek.com/v1"),
                timeout=timeout,
            ).set_tracer(tracer)
        if provider_name == "claude":
            return ClaudeLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "claude-3-5-sonnet-latest"),
                base_url=provider_settings.get("base_url", "https://api.anthropic.com"),
                timeout=timeout,
                max_tokens=int(provider_settings.get("max_tokens", config.get("llm.max_tokens", 1024))),
                anthropic_version=provider_settings.get(
                    "anthropic_version",
                    config.get("llm.anthropic_version", "2023-06-01"),
                ),
            ).set_tracer(tracer)
        raise build_error(LLM_PROVIDER_NOT_FOUND, f"Unsupported LLM provider: {provider_name}")

    @staticmethod
    def _build_storage_registry(config: JsonConfig) -> StorageRegistry:
        seed_documents = load_seed_documents(
            config.get("storage.file.path", "testing/runtime/nanoagent_soul.json")
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
            databases["local_storage"] = "testing/runtime/nanoagent_local_storage.db"
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
