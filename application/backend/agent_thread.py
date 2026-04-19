from __future__ import annotations

import os
import threading
from typing import Callable

from agent import AgentExecutor
from config import ConfigValueReader, JsonConfig
from context.session import Session
from queue.message_queue import AgentToUserQueue, UserToAgentQueue
from schemas import (
    AGENT_THREAD_ERROR,
    AgentError,
    ChatMessage,
    LLM_ALL_PROVIDERS_FAILED,
    STORAGE_CONFIG_ERROR,
    SessionStatus,
    build_error,
)
from storage import ChromaDBStorage, MySQLStorage, SQLiteStorage, StorageRegistry
from storage.bootstrap_documents import load_seed_documents
from tracing import Span, Tracer
from tools import ToolRegistry, create_default_tool_registry
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
        self._config = config
        self._config_value_reader = ConfigValueReader(config)
        self._stop_event = stop_event
        self._stop_callback = stop_callback
        self._logger = logger
        self._session = Session()
        self._user_message_wait_timeout_seconds = self._config_value_reader.positive_float(
            "agent.latency.agent_user_message_wait_timeout_seconds",
            2.0,
        )
        self._max_react_attempt_iterations = int(
            self._config.get("agent.max_react_attempt_iterations", 60)
        )
        self._storage_registry: StorageRegistry | None = None
        self._tool_registry: ToolRegistry | None = None
        self._executor: AgentExecutor | None = None
        self._tracer: Tracer | None = None
        self._session_span: Span | None = None
        self._load_tool_config()
        self._load_tracing_config()
        try:
            self._tracer = self._build_tracer()
            self._storage_registry = self._build_storage_registry()
            self._tool_registry = self._build_tool_registry()
            self._executor = self._build_executor()
        except Exception:
            self.release_resources()
            raise

    def stop(self) -> None:
        self._stop_callback(self.name)

    def reset(self) -> None:
        self._finish_session_trace()
        if self._executor is not None:
            self._executor.reset(archive_current_task=False)

    def release_resources(self) -> None:
        self.reset()
        if self._executor is not None:
            self._executor.release_resources()
        if self._storage_registry is not None:
            self._storage_registry.close_all()
        self._executor = None
        self._tool_registry = None
        self._storage_registry = None
        self._session_span = None

    def _build_executor(self) -> AgentExecutor:
        return AgentExecutor(
            session=self._session,
            tool_registry=self._tool_registry,
            storage_registry=self._storage_registry,
            config=self._config,
            tracer=self._tracer,
            logger=self._logger,
        )

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
            collections = [str(c).strip() for c in configured if str(c).strip()]
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

    def _load_tool_config(self) -> None:
        self._tool_retry_max_attempts = int(self._config.get("tools.retry.max_attempts", 4))
        self._tool_retry_delays = self._config_value_reader.retry_delays(
            "tools.retry.backoff_seconds",
        )

    def _build_tool_registry(self) -> ToolRegistry:
        package_name = self._config.get("tools.package", "tools.impl")
        if not isinstance(package_name, str) or not package_name.strip():
            package_name = "tools.impl"
        module_names = self._config.get("tools.modules", [])
        if not isinstance(module_names, list):
            module_names = []
        return create_default_tool_registry(
            module_names=module_names,
            package_name=package_name,
            timeout_retry_max_attempts=self._tool_retry_max_attempts,
            timeout_retry_delays=self._tool_retry_delays,
            tracer=self._tracer,
            logger=self._logger,
        )

    def run(self) -> None:
        try:
            while self._is_running():
                session_status = self._session.get_status()
                if (
                    session_status == SessionStatus.IN_PROGRESS
                    and self._executor is not None
                    and self._executor.get_iterations() > self._max_react_attempt_iterations
                ):
                    completion_message = ChatMessage(
                        role="assistant",
                        content="Sorry, this question is too hard, i can not solve",
                        metadata={
                            "session_status": SessionStatus.NEW_TASK,
                            "task_completed": True,
                        },
                    )
                    self._agent_to_user_queue.send_agent_message(completion_message)
                    self._complete_current_task(archive_current_task=False)
                    continue

                incoming_message = self._wait_for_user_message(session_status)
                if not self._is_running():
                    break
                if incoming_message is not None:
                    if session_status == SessionStatus.NEW_TASK:
                        self._start_session_trace(incoming_message)
                        self._executor.begin_session()
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
                        self._record_user_input_trace(incoming_message, input_type="new task")
                    else:
                        self._record_user_input_trace(incoming_message, input_type="hint")

                try:
                    execution_result = self._executor.run(incoming_message)
                    for message in execution_result.user_messages:
                        self._agent_to_user_queue.send_agent_message(message)

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
                        continue

                    if execution_result.error is not None:
                        self._logger.error(
                            "Agent execution returned an internal error",
                            zap.any("trace_id", None if self._tracer is None else self._tracer.current_trace_id()),
                            zap.any("span_id", None if self._tracer is None else self._tracer.current_span_id()),
                            zap.any("error", execution_result.error),
                        )
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
                "task": user_message.content,
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
        if self._executor is not None:
            self._executor.reset(archive_current_task=archive_current_task)

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
