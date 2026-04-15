from __future__ import annotations

import os
import threading
from typing import Callable

from agent import Agent, ReActAgent
from agent.impl import ReActAgentContext
from config import ConfigValueReader, JsonConfig
from context.agent_context import AgentContext
from context.formatter import MessageFormatter
from context.session import Session
from llm import (
    BaseLLMClient,
    ClaudeLLMClient,
    DeepSeekLLMClient,
    FallbackLLMClient,
    LLMProviderRegistry,
    OpenAILLMClient,
    QwenLLMClient,
)
from queue.message_queue import AgentToUserQueue, UserToAgentQueue
from schemas import (
    AGENT_THREAD_ERROR,
    AgentError,
    ChatMessage,
    LLM_ALL_PROVIDERS_FAILED,
    LLM_PROVIDER_NOT_FOUND,
    STORAGE_CONFIG_ERROR,
    SessionStatus,
    build_error,
)
from storage import ChromaDBStorage, MySQLStorage, SQLiteStorage, StorageRegistry
from storage.bootstrap_documents import load_seed_documents
from tracing import Span, Tracer
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
from utils.thread_event import ThreadEvent


class AgentThread(threading.Thread):
    def __init__(
        self,
        user_to_agent_queue: UserToAgentQueue,
        agent_to_user_queue: AgentToUserQueue,
        config: JsonConfig,
        stop_event: ThreadEvent,
        stop_callback: Callable[[str | None], None],
        logger: Logger,
    ) -> None:
        super().__init__(name="AgentThread", daemon=False)
        self._user_to_agent_queue = user_to_agent_queue
        self._agent_to_user_queue = agent_to_user_queue
        self._agent_context: AgentContext = self._build_agent_context()
        self._session = Session()
        self._config = config
        self._config_value_reader = ConfigValueReader(config)
        self._stop_event = stop_event
        self._stop_callback = stop_callback
        self._logger = logger
        self._user_message_wait_timeout_seconds = self._config_value_reader.positive_float(
            "agent.latency.agent_user_message_wait_timeout_seconds",
            2.0,
        )
        self._storage_registry: StorageRegistry | None = None
        self._message_formatter: MessageFormatter | None = None
        self._tool_registry: ToolRegistry | None = None
        self._llm_client: BaseLLMClient | None = None
        self._agent: Agent | None = None
        self._tracer: Tracer | None = None
        self._session_span: Span | None = None
        self._base_system_prompt = self._agent_context.get_system_prompt()
        self._load_agent_config()
        self._load_llm_config()
        self._load_tool_config()
        self._load_tracing_config()
        try:
            self._tracer = self._build_tracer()
            self._storage_registry = self._build_storage_registry()
            self._message_formatter = self._build_message_formatter()
            self._tool_registry = self._build_tool_registry()
            self._base_system_prompt = self._agent_context.get_system_prompt()
            self._llm_client = self._build_llm_client()
            self._agent = self._build_agent()
        except Exception:
            self.release_resources()
            self.stop()
            raise

    @staticmethod
    def _build_agent_context() -> AgentContext:
        return ReActAgentContext()

    def stop(self) -> None:
        self._stop_callback(self.name)

    def reset(self) -> None:
        self._finish_session_trace()
        if self._agent is not None:
            self._agent.reset(archive_current_task=False)
        self._restore_base_system_prompt()

    def release_resources(self) -> None:
        self.reset()
        if self._agent is not None:
            self._agent.release_resources()
        self._agent_context.release()
        if self._storage_registry is not None:
            self._storage_registry.close_all()
        self._agent = None
        self._llm_client = None
        self._tool_registry = None
        self._message_formatter = None
        self._storage_registry = None
        self._session_span = None

    def _build_storage_registry(self) -> StorageRegistry:
        seed_documents = load_seed_documents(
            self._config.get("storage.file.path", "runtime/nanoagent_soul.json")
        )
        sqlite_databases = self._build_sqlite_databases()
        sqlite_storage = SQLiteStorage(sqlite_databases)
        storages = [sqlite_storage]

        chromadb_path = self._config.get("storage.chromadb.persist_directory")
        if chromadb_path:
            chromadb_collections = self._build_chromadb_collections()
            chromadb_storage = ChromaDBStorage(
                persist_directory=chromadb_path,
                collections=chromadb_collections,
            )
            bootstrap_collection = self._config.get("storage.chromadb.bootstrap_collection")
            if isinstance(bootstrap_collection, str) and bootstrap_collection.strip():
                if not chromadb_storage.get_documents(bootstrap_collection):
                    chromadb_storage.upsert_documents(bootstrap_collection, seed_documents)
            storages.append(chromadb_storage)

        mysql_host = str(self._config.get("storage.mysql.host", "")).strip()
        if mysql_host:
            mysql_storage = MySQLStorage(
                host=mysql_host,
                port=int(self._config.get("storage.mysql.port", 3306)),
                user=os.getenv("MYSQL_USER", ""),
                password=os.getenv("MYSQL_PASSWORD", ""),
                allowed_databases=self._build_mysql_databases(),
                charset=str(self._config.get("storage.mysql.charset", "utf8mb4")),
            )
            storages.append(mysql_storage)

        return StorageRegistry(storages)

    def _build_sqlite_databases(self) -> dict[str, str]:
        sqlite_config = self._config.get("storage.sqlite", {})
        if not isinstance(sqlite_config, dict):
            sqlite_config = {}
        configured = sqlite_config.get("allowed_databases")
        if configured is None:
            configured = sqlite_config.get("databases")
        databases: dict[str, str] = {}
        if isinstance(configured, dict):
            for name, path in configured.items():
                if str(name).strip() and str(path).strip():
                    databases[str(name).strip()] = str(path).strip()
        fallback_path = str(sqlite_config.get("path", "")).strip()
        if fallback_path:
            databases.setdefault(self._derive_sqlite_alias(fallback_path), fallback_path)
        if not databases:
            databases["local_storage"] = "runtime/nanoagent_local_storage.db"
        return databases

    def _build_mysql_databases(self) -> list[str]:
        mysql_config = self._config.get("storage.mysql", {})
        if not isinstance(mysql_config, dict):
            mysql_config = {}
        configured = mysql_config.get("allowed_databases")
        if isinstance(configured, list):
            databases = [str(database).strip() for database in configured if str(database).strip()]
            if databases:
                return databases
        fallback_database = str(mysql_config.get("database", "")).strip()
        if fallback_database:
            return [fallback_database]
        raise build_error(
            STORAGE_CONFIG_ERROR,
            "MySQL storage requires `storage.mysql.allowed_databases` or `storage.mysql.database`.",
        )

    def _build_chromadb_collections(self) -> list[str]:
        chromadb_config = self._config.get("storage.chromadb", {})
        if not isinstance(chromadb_config, dict):
            chromadb_config = {}
        configured = chromadb_config.get("allowed_collections")
        if configured is None:
            configured = chromadb_config.get("collections")
        if isinstance(configured, list):
            collections = [str(collection).strip() for collection in configured if str(collection).strip()]
            if collections:
                return collections
        fallback_collection = str(chromadb_config.get("collection_name", "")).strip()
        if fallback_collection:
            return [fallback_collection]
        return ["agent_documents"]

    @staticmethod
    def _derive_sqlite_alias(path_value: str) -> str:
        path = str(path_value).strip()
        if path.endswith(".db"):
            path = path[:-3]
        return path.rsplit("/", 1)[-1] or "sqlite"

    def _load_tracing_config(self) -> None:
        self._tracing_enabled = bool(self._config.get("tracing.enabled", True))
        self._tracing_output_path = self._config.get("tracing.output_path", "runtime/tracing/traces.jsonl")
        self._tracing_payload_redaction_enabled = bool(
            self._config.get(
                "tracing.payload_redaction_enabled",
                not bool(self._config.get("tracing.capture_payloads", False)),
            )
        )
        self._tracing_max_content_length = self._config_value_reader.positive_int(
            "tracing.max_content_length",
            default=1000,
        )

    def _build_tracer(self) -> Tracer:
        return Tracer(
            enabled=self._tracing_enabled,
            output_path=self._tracing_output_path,
            payload_redaction_enabled=self._tracing_payload_redaction_enabled,
            max_content_length=self._tracing_max_content_length,
        )

    def _load_agent_config(self) -> None:
        self._max_tool_iterations = int(self._config.get("agent.max_tool_iterations", 3))
        self._max_react_attempt_iterations = int(
            self._config.get("agent.max_react_attempt_iterations", 60)
        )

    def _load_llm_config(self) -> None:
        self._llm_retry_max_attempts = int(self._config.get("llm.retry.max_attempts", 4))
        self._llm_retry_delays = self._config_value_reader.retry_delays(
            "llm.retry.backoff_seconds",
        )
        self._llm_context_trimming_enabled = bool(
            self._config.get("llm.context_trimming.enabled", True)
        )
        self._llm_context_max_messages = self._config_value_reader.positive_int(
            "llm.context_trimming.max_messages",
            default=40,
        )

    def _load_tool_config(self) -> None:
        self._tool_retry_max_attempts = int(self._config.get("tools.retry.max_attempts", 4))
        self._tool_retry_delays = self._config_value_reader.retry_delays(
            "tools.retry.backoff_seconds",
        )

    def _build_message_formatter(self) -> MessageFormatter:
        if not self._llm_context_trimming_enabled:
            return MessageFormatter(max_messages=None)
        return MessageFormatter(max_messages=self._llm_context_max_messages)

    def _build_tool_registry(self) -> ToolRegistry:
        package_name = self._config.get("tools.package", "tools.impl")
        if not isinstance(package_name, str) or not package_name.strip():
            package_name = "tools.impl"
        module_names = self._config.get("tools.modules", [])
        if not isinstance(module_names, list):
            module_names = []
        registry = create_default_tool_registry(
            module_names=module_names,
            package_name=package_name,
            timeout_retry_max_attempts=self._tool_retry_max_attempts,
            timeout_retry_delays=self._tool_retry_delays,
            tracer=self._tracer,
        )
        self._register_storage_tools(registry)
        return registry

    def _register_storage_tools(self, registry: ToolRegistry) -> None:
        if self._storage_registry is None:
            return

        sql_tool_lines: list[str] = []
        vector_tool_lines: list[str] = []
        for backend_name in self._storage_registry.list_backends():
            storage = self._storage_registry.get(backend_name)
            if backend_name in {"sqlite", "mysql"}:
                schema_tool_name = build_sql_schema_tool_name(backend_name)
                schema_description = build_sql_schema_tool_description(backend_name)
                registry.register(
                    SQLSchemaTool(
                        name=schema_tool_name,
                        description=schema_description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                tool_name = build_sql_query_tool_name(backend_name)
                description = build_sql_query_tool_description(backend_name)
                registry.register(
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
                registry.register(
                    VectorSchemaTool(
                        name=schema_tool_name,
                        description=schema_description,
                        storage=storage,
                        backend_name=backend_name,
                    )
                )
                tool_name = build_vector_search_tool_name(backend_name)
                description = build_vector_search_tool_description(backend_name)
                registry.register(
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
                f"{'\n'.join(sql_tool_lines)}"
            )

        if vector_tool_lines:
            self._agent_context.append_system_prompt(
                "\n\nVector search tool guide:\n"
                "Use vector search tools for semantic retrieval from indexed text collections.\n"
                "When you are unsure which collection to use, inspect the available collections first.\n"
                "Choose the most relevant authorized collection for the task.\n"
                "Prefer them when the task needs fuzzy matching, semantic lookup, or concept-level retrieval.\n"
                f"{'\n'.join(vector_tool_lines)}"
            )

    def _restore_base_system_prompt(self) -> None:
        if self._agent_context.get_system_prompt() == self._base_system_prompt:
            return
        self._agent_context.release()
        self._agent_context = self._build_agent_context()
        self._agent_context.append_system_prompt(self._base_system_prompt)

    def _build_llm_client(self) -> BaseLLMClient:
        provider_priority = self._config.get("llm.priority_chain", ["deepseek"])
        if not isinstance(provider_priority, list) or not provider_priority:
            provider_priority = ["deepseek"]

        registry = LLMProviderRegistry()
        for provider_name in provider_priority:
            registry.register(self._build_provider(provider_name))
        return FallbackLLMClient(
            registry=registry,
            provider_priority=provider_priority,
            max_attempts=self._llm_retry_max_attempts,
            retry_delays=self._llm_retry_delays,
            enable_provider_fallback=bool(self._config.get("llm.enable_provider_fallback", False)),
        ).set_tracer(self._tracer)

    def _build_provider(self, provider_name: str) -> BaseLLMClient:
        providers = self._config.get("llm.providers", {})
        if not isinstance(providers, dict):
            providers = {}
        provider_settings = providers.get(provider_name, {})
        if not isinstance(provider_settings, dict):
            provider_settings = {}
        overrides = dict(provider_settings)
        api_key = overrides.get("api_key")
        timeout = float(overrides.get("timeout", self._config.get("llm.timeout", 60.0)))

        if provider_name == "openai":
            return OpenAILLMClient.from_settings(
                api_key=api_key,
                model=overrides.get("model", "gpt-4o-mini"),
                base_url=overrides.get("base_url", "https://api.openai.com/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "qwen":
            return QwenLLMClient.from_settings(
                api_key=api_key,
                model=overrides.get("model", "qwen-plus"),
                base_url=overrides.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "deepseek":
            return DeepSeekLLMClient.from_settings(
                api_key=api_key,
                model=overrides.get("model", "deepseek-chat"),
                base_url=overrides.get("base_url", "https://api.deepseek.com/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "claude":
            return ClaudeLLMClient.from_settings(
                api_key=api_key,
                model=overrides.get("model", "claude-3-5-sonnet-latest"),
                base_url=overrides.get("base_url", "https://api.anthropic.com"),
                timeout=timeout,
                max_tokens=int(overrides.get("max_tokens", self._config.get("llm.max_tokens", 1024))),
                anthropic_version=overrides.get(
                    "anthropic_version",
                    self._config.get("llm.anthropic_version", "2023-06-01"),
                ),
            ).set_tracer(self._tracer)
        raise build_error(LLM_PROVIDER_NOT_FOUND, f"Unsupported LLM provider: {provider_name}")

    def _build_agent(self) -> Agent:
        return ReActAgent(
            agent_context=self._agent_context,
            session=self._session,
            message_formatter=self._message_formatter,
            llm_client=self._llm_client,
            tool_registry=self._tool_registry,
            max_tool_iterations=self._max_tool_iterations,
        )

    def run(self) -> None:
        try:
            while self._is_running():
                session_status = self._session.get_status()
                if (
                    session_status == SessionStatus.IN_PROGRESS
                    and self._agent is not None
                    and self._agent.get_react_attempt_iterations() > self._max_react_attempt_iterations
                ):#有限处理，防止无限循环
                    completion_message = ChatMessage(
                        role="assistant",
                        content="Sorry, this question is too hard, i can not solve",
                        metadata={
                            "session_status": SessionStatus.NEW_TASK,
                            "task_completed": True,
                        },
                    )
                    self._agent_to_user_queue.send_agent_message(completion_message)
                    self._complete_current_task(
                        archive_current_task=False,
                    )
                    break

                incoming_message = self._wait_for_user_message(session_status)
                if not self._is_running():
                    break
                if incoming_message is not None:
                    if session_status == SessionStatus.NEW_TASK: 
                        self._start_session_trace(incoming_message)
                        self._agent.begin_session()
                        self._agent_to_user_queue.send_agent_message(
                            ChatMessage(
                                role="assistant",
                                content="",
                                metadata={
                                    "control": True,
                                    "session_status": SessionStatus.IN_PROGRESS,
                                    "task_completed": False,
                                },
                            )
                        )
                        session_status = self._session.get_status()
                    else:
                        self._record_user_input_trace(incoming_message, input_type="hint")

                try:
                    execution_result = self._agent.run(session_status, incoming_message)
                    for message in execution_result.user_messages:
                        self._agent_to_user_queue.send_agent_message(message)

                    if execution_result.error is not None:
                        self._logger.error(
                            "Agent execution returned an internal error",
                            zap.any("trace_id", None if self._tracer is None else self._tracer.current_trace_id()),
                            zap.any("span_id", None if self._tracer is None else self._tracer.current_span_id()),
                            zap.any("error", execution_result.error),
                        )

                    if execution_result.task_completed:
                        self._agent_to_user_queue.send_agent_message(
                            ChatMessage(
                                role="assistant",
                                content="",
                                metadata={
                                    "control": True,
                                    "session_status": SessionStatus.NEW_TASK,
                                    "task_completed": True,
                                },
                            )
                        )
                        self._complete_current_task(
                            archive_current_task=execution_result.error is None,
                            error=execution_result.error,
                        )
                        break

                    if self._is_hard_error(execution_result.error):
                        self._finish_session_trace(error=execution_result.error)
                        break
                except Exception as exc:
                    normalized_error = self._normalize_error(exc)
                    self._finish_session_trace(error=normalized_error)
                    self._logger.error(
                        "Agent thread execution failed",
                        zap.any("trace_id", None if self._tracer is None else self._tracer.current_trace_id()),
                        zap.any("span_id", None if self._tracer is None else self._tracer.current_span_id()),
                        zap.any("error", normalized_error),
                    )
                    break
        except Exception as exc:
            self._finish_session_trace(error=exc)
            self._logger.error("Agent thread crashed", zap.any("error", exc))
        finally:
            self.release_resources()
            self.stop()

    def _start_session_trace(self, user_message: ChatMessage) -> None:
        if self._tracer is None or self._session_span is not None:
            return
        self._session_span = self._tracer.start_trace(
            "session",
            attributes={
                "thread": self.name,
                "session_status": self._session.get_status(),
                "user_message": user_message.content,
            },
        )

    def _finish_session_trace(self, error: Exception | AgentError | None = None) -> None:
        if self._session_span is None:
            return
        status = "error" if error is not None else "ok"
        self._session_span.finish(status=status, error=error)
        self._session_span = None

    def _complete_current_task(
        self,
        archive_current_task: bool,
        error: Exception | AgentError | None = None,
    ) -> None:
        self._finish_session_trace(error=error)
        if self._agent is not None:
            self._agent.reset(archive_current_task=archive_current_task)
        self._restore_base_system_prompt()

    def _record_user_input_trace(
        self,
        user_message: ChatMessage,
        input_type: str,
    ) -> None:
        if self._tracer is None:
            return
        with self._tracer.start_span(
            name="user.input",
            type="input",
            attributes={
                "input_type": input_type,
                "role": user_message.role,
                "content": user_message.content,
            },
        ):
            return

    def _wait_for_user_message(
        self,
        session_status: SessionStatus,
    ) -> ChatMessage | None:
        if session_status == SessionStatus.NEW_TASK:
            return self._user_to_agent_queue.get_user_message(timeout=None)
        return self._user_to_agent_queue.get_user_message(
            timeout=self._user_message_wait_timeout_seconds
        )

    @staticmethod
    def _normalize_error(exc: Exception | AgentError) -> AgentError:
        if isinstance(exc, AgentError):
            return exc
        return build_error(AGENT_THREAD_ERROR, str(exc))

    @staticmethod
    def _is_hard_error(error: AgentError | None) -> bool:
        return error is not None and error.code == LLM_ALL_PROVIDERS_FAILED

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._is_any_queue_closed()

    def _is_any_queue_closed(self) -> bool:
        return self._user_to_agent_queue.is_closed() or self._agent_to_user_queue.is_closed()
