from __future__ import annotations

import os
import random
import time
from typing import TYPE_CHECKING, Callable

from config import ConfigValueReader
from context.manager import AgentContext
from schemas import (
    AGENT_EXECUTION_ERROR,
    AGENT_STRATEGY_NOT_FOUND,
    AgentError,
    AgentExecutionResult,
    ErrorCategory,
    LLMError,
    LLMMessage,
    LLM_ALL_PROVIDERS_FAILED,
    LLM_PROVIDER_NOT_FOUND,
    LLMRequest,
    LLMResponse,
    STORAGE_CONFIG_ERROR,
    UIMessage,
    build_error,
)
from schemas.message_convert import ui_to_llm
from infra.db import ChromaDBStorage, MySQLStorage, SQLiteStorage, StorageRegistry
from infra.db.bootstrap_documents import load_seed_documents
from execution.models.strategies.decision import FinalAnswer, ResponseTruncated
from execution.models.strategies.impl import ReActStrategy
from llm import (
    BaseLLMClient,
    ClaudeLLMClient,
    DeepSeekLLMClient,
    GLMLLMClient,
    KimiLLMClient,
    LLMProviderRegistry,
    MinMaxLLMClient,
    OpenAILLMClient,
    QwenLLMClient,
    RetryConfig,
    SingleProviderClient,
)
from llm.routing import LLMProviderRouter, RoutingDecision
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
from context.budget.token_budget_manager import TokenBudgetManagerFactory
from context.estimator.token_estimator import TokenEstimatorFactory
from context.truncation.token_truncation import TruncatorFactory, ContextTruncator
from utils.log.log import Logger, zap

if TYPE_CHECKING:
    from config import JsonConfig
    from execution.models.strategies.strategy import Strategy
    from infra.observability.tracing import Tracer


# ---------------------------------------------------------------------------
# Module-level helpers for _call_llm
# ---------------------------------------------------------------------------

def _try_trim_context(request: LLMRequest) -> LLMRequest | None:
    """Drop the two oldest non-system messages. Returns None if already too short."""
    if len(request.messages) < 2:
        return None
    return LLMRequest(
        system_prompt=request.system_prompt,
        messages=request.messages[2:],
        tools=request.tools,
    )


def _try_self_repair(
    client: "SingleProviderClient",
    request: LLMRequest,
    error: LLMError,
) -> LLMResponse | None:
    repair_prompt = (
        "Your previous output could not be parsed by the client. "
        "Please regenerate a valid response following the expected tool-call/text format. "
        "Below is the parser error and raw output details captured by client.\n\n"
        f"{error.message}"
    )
    repaired_request = LLMRequest(
        system_prompt=request.system_prompt,
        messages=[*request.messages, LLMMessage(role="user", content=repair_prompt)],
        tools=request.tools,
    )
    try:
        return client.generate(repaired_request)
    except Exception:
        return None


def _backoff(cfg: RetryConfig, attempt_idx: int) -> float:
    cap = min(cfg.retry_base * (2 ** attempt_idx), cfg.retry_max_delay)
    return random.uniform(0, cap)


class AgentExecutor:
    def __init__(
        self,
        config: JsonConfig,
        tracer: Tracer | None,
        logger: Logger,
    ) -> None:
        self._logger = logger
        self._config = config
        self._agent_context = AgentContext()
        self._retry_config = RetryConfig(
            retry_base=float(config.get("llm.retry.base", 0.5)),
            retry_max_delay=float(config.get("llm.retry.max_delay", 60.0)),
            retry_max_attempts=int(config.get("llm.retry.max_attempts", 5)),
        )

        config_reader = ConfigValueReader(config)
        self._tracer = tracer
        self._storage_registry = self._build_storage_registry(config)
        self._tool_registry = self._build_tool_registry(config, config_reader, tracer, logger)
        self._llm_provider_router = self._build_llm_provider_router(config, tracer)
        self._strategy = self._build_strategy(config)
        self._register_storage_tools(self._storage_registry)
        self._truncator = self._build_truncator(config, tracer)

    # ------------------------------------------------------------------
    # Conversation interfaces (for Strategy use)
    # ------------------------------------------------------------------

    def append_conversation(self, message: LLMMessage) -> None:
        self._agent_context.append_conversation_message(message)

    def get_conversation(self) -> list[LLMMessage]:
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
        user_message: UIMessage | None,
    ) -> AgentExecutionResult:
        self._logger.info(
            "AgentExecutor run start",
            zap.any("has_user_message", user_message is not None),
            zap.any("user_message", user_message.content[:200] if user_message else None),
        )

        if user_message is not None and user_message.content.strip():
            self.append_conversation(LLMMessage(role="user", content=user_message.content.strip()))

        result = self._execute()

        self._logger.info(
            "AgentExecutor run complete",
            zap.any("task_completed", result.task_completed),
            zap.any("has_error", result.error is not None),
            zap.any("error", str(result.error) if result.error else None),
        )
        return result

    def _execute(self) -> AgentExecutionResult:#TODO 这里的异常处理决策不清晰和完备
        user_messages: list[UIMessage] = []

        request = self._strategy.build_llm_request(
            agent_context=self._agent_context,
            tool_registry=self._tool_registry,
        )

        routing_decision = self._llm_provider_router.route(request)

        try:
            llm_response = self._call_llm(request, routing_decision)
        except AgentError as exc:
            return AgentExecutionResult(
                user_messages=user_messages,
                error=exc if exc.code == LLM_ALL_PROVIDERS_FAILED else build_error(AGENT_EXECUTION_ERROR, str(exc)),
            )

        decision = self._strategy.parse_llm_response(llm_response)

        if isinstance(decision, ResponseTruncated):
            self.append_conversation(ui_to_llm(decision.message))
            user_messages.append(decision.message)
            return AgentExecutionResult(user_messages=user_messages, error=decision.error)

        if isinstance(decision, FinalAnswer):
            self.append_conversation(ui_to_llm(decision.message))
            user_messages.append(decision.message)
            return AgentExecutionResult(user_messages=user_messages, task_completed=True)

        # InvokeTools
        self.append_conversation(decision.assistant_message)
        llm_content = decision.assistant_message.content.strip()
        if llm_content:
            user_messages.append(UIMessage(
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
                tool_call=tool_call,
                result=result,
            )
            self.append_conversation(observation)
            user_messages.append(UIMessage(
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
        return AgentExecutionResult(user_messages=user_messages, task_completed=False)

    def _call_llm(self, request: LLMRequest, routing_decision: RoutingDecision) -> LLMResponse:
        cfg = self._retry_config
        failures: list[str] = []

        self._logger.info(
            "LLM call start",
            zap.any("primary", routing_decision.primary.provider_name),
            zap.any("fallbacks", [c.provider_name for c in routing_decision.fallbacks]),
        )

        for client in [routing_decision.primary, *routing_decision.fallbacks]:
            provider_name = client.provider_name
            estimator = TokenEstimatorFactory.get_estimator(provider_name)
            total_budget = self._get_provider_context_window(provider_name)
            with self._start_span("context.truncate", {"provider": provider_name, "total_budget": total_budget}):
                trunc_result = self._truncator.truncate(request, total_budget, estimator)
            current_request = trunc_result.request
            attempt = 0

            while attempt < cfg.retry_max_attempts:
                try:
                    response = client.generate(current_request)
                    if trunc_result.compacted_messages is not None:
                        self._agent_context.replace_conversation_history(trunc_result.compacted_messages)
                    self._logger.info(
                        "LLM generate success",
                        zap.any("provider", provider_name),
                        zap.any("attempt", attempt + 1),
                    )
                    return response

                except LLMError as exc:
                    self._logger.error(
                        "LLM error",
                        zap.any("provider", provider_name),
                        zap.any("category", exc.category.value),
                        zap.any("code", exc.code.value),
                        zap.any("attempt", attempt + 1),
                        zap.any("message", exc.message),
                    )
                    failures.append(f"{provider_name}[{exc.code.value}]: {exc.message}")

                    if exc.category in {ErrorCategory.AUTH, ErrorCategory.CONFIG}:
                        break  # fatal for this provider, skip to next

                    if exc.category == ErrorCategory.CONTEXT:
                        trimmed = _try_trim_context(current_request)
                        if trimmed is None:
                            self._logger.error(
                                "Context too long and cannot be trimmed",
                                zap.any("provider", provider_name),
                            )
                            break
                        self._logger.info(
                            "Context trimmed, retrying",
                            zap.any("provider", provider_name),
                            zap.any("remaining_messages", len(trimmed.messages)),
                        )
                        current_request = trimmed
                        continue  # retry without incrementing attempt

                    if exc.category == ErrorCategory.RESPONSE:
                        self._logger.info(
                            "Attempting self-repair",
                            zap.any("provider", provider_name),
                            zap.any("error", exc.message[:200]),
                        )
                        repaired = _try_self_repair(client, current_request, exc)
                        if repaired is not None:
                            self._logger.info("Self-repair succeeded", zap.any("provider", provider_name))
                            return repaired
                        self._logger.warning("Self-repair failed", zap.any("provider", provider_name))
                        break  # self-repair failed, skip to next provider

                    # TRANSIENT or RATE_LIMIT — backoff and retry
                    attempt += 1
                    if attempt < cfg.retry_max_attempts:
                        delay = exc.retry_after if exc.retry_after is not None else _backoff(cfg, attempt - 1)
                        self._logger.info(
                            "LLM retry backoff",
                            zap.any("provider", provider_name),
                            zap.any("attempt", attempt),
                            zap.any("delay_seconds", round(delay, 2)),
                        )
                        time.sleep(delay)

        raise build_error(
            LLM_ALL_PROVIDERS_FAILED,
            "All attempted LLM providers failed. " + " | ".join(failures),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_provider_context_window(self, provider_name: str) -> int:
        window = self._config.get(f"llm.provider_settings.{provider_name}.context_window")
        if window is not None:
            return int(window)
        defaults = {"claude": 200000, "deepseek": 64000, "openai": 128000}
        return defaults.get(provider_name, 32000)

    def _start_span(self, name: str, attributes: dict | None = None):
        from infra.observability.tracing import Span
        if self._tracer is None:
            return Span(None)
        return self._tracer.start_span(name=name, type="agent", attributes=attributes)

    def _build_strategy(self, config: JsonConfig) -> Strategy:
        strategy_name = str(config.get("agent.strategy", "react")).strip()
        self._logger.info("Agent strategy selected", zap.any("strategy", strategy_name))
        if strategy_name == "react":
            strategy = ReActStrategy()
        else:
            raise build_error(
                AGENT_STRATEGY_NOT_FOUND,
                f"Unsupported agent strategy: {strategy_name}",
            )
        return strategy

    def _build_truncator(self, config: JsonConfig, tracer: Tracer | None) -> ContextTruncator:
        strategy_name = str(config.get("agent.strategy", "react")).strip()
        budget_manager = TokenBudgetManagerFactory.create(strategy_name, config)
        def llm_client_factory(provider_name: str) -> BaseLLMClient:
            return AgentExecutor._build_provider(provider_name, config, tracer)

        return TruncatorFactory.create(strategy_name, budget_manager, llm_client_factory, self._logger, config)

    @staticmethod
    def _build_llm_provider_router(config: JsonConfig, tracer: Tracer | None) -> LLMProviderRouter:
        priority_chain = config.get("llm.priority_chain", ["deepseek"])
        if not isinstance(priority_chain, list) or not priority_chain:
            priority_chain = ["deepseek"]

        registry = LLMProviderRegistry()
        for name in priority_chain:
            registry.register(AgentExecutor._build_provider(name, config, tracer))

        return LLMProviderRouter(
            registry=registry,
            priority_chain=priority_chain,
            enable_fallback=bool(config.get("llm.enable_provider_fallback", False)),
        )

    @staticmethod
    def _build_provider(provider_name: str, config: JsonConfig, tracer: Tracer | None) -> BaseLLMClient:
        provider_settings = config.get(f"llm.provider_settings.{provider_name}", {})
        if not isinstance(provider_settings, dict):
            provider_settings = {}
        timeout = float(provider_settings.get("timeout", 60.0))
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
        if provider_name == "minmax":
            return MinMaxLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "MiniMax-Text-01"),
                base_url=provider_settings.get("base_url", "https://api.minimax.chat/v1"),
                timeout=timeout,
            ).set_tracer(tracer)
        if provider_name == "glm":
            return GLMLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "glm-4"),
                base_url=provider_settings.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                timeout=timeout,
            ).set_tracer(tracer)
        if provider_name == "kimi":
            return KimiLLMClient.from_settings(
                api_key=api_key,
                model=provider_settings.get("model", "moonshot-v1-8k"),
                base_url=provider_settings.get("base_url", "https://api.moonshot.cn/v1"),
                timeout=timeout,
            ).set_tracer(tracer)
        raise build_error(LLM_PROVIDER_NOT_FOUND, f"Unsupported LLM provider: {provider_name}")

    @staticmethod
    def _build_storage_registry(config: JsonConfig) -> StorageRegistry:
        seed_documents = load_seed_documents(
            config.get("storage.file.path", "tests/runtime/nanoagent_soul.json")
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
            databases["local_storage"] = "var/storage/nanoagent_local_storage.db"
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
        for backend_name in storage_registry.list_backends():
            storage = storage_registry.get(backend_name)
            if backend_name in {"sqlite", "mysql"}:
                resources = ", ".join(storage.list_resources()) or "<none>"
                self._tool_registry.register(SQLSchemaTool(
                    name=build_sql_schema_tool_name(backend_name),
                    description=build_sql_schema_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
                self._tool_registry.register(SQLQueryTool(
                    name=build_sql_query_tool_name(backend_name),
                    description=build_sql_query_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
                continue

            if backend_name == "chromadb":
                resources = ", ".join(storage.list_resources()) or "<none>"
                self._tool_registry.register(VectorSchemaTool(
                    name=build_vector_schema_tool_name(backend_name),
                    description=build_vector_schema_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
                self._tool_registry.register(VectorSearchTool(
                    name=build_vector_search_tool_name(backend_name),
                    description=build_vector_search_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))


class StepOrchestrationService:
    """步骤层执行入口，封装 AgentExecutor 的 ReAct 循环。"""

    def __init__(self, executor: AgentExecutor, max_iterations: int = 60) -> None:
        self._executor = executor
        self._max_iterations = max_iterations

    def reset(self) -> None:
        self._executor.reset(archive_current_task=False)

    def release_resources(self) -> None:
        self._executor.release_resources()

    def run_step(
        self,
        task_step: "TaskStep",
        on_message: Callable[[UIMessage], None],
    ) -> str:
        user_message: UIMessage | None = UIMessage(role="user", content=task_step.goal)
        last_content = ""
        iterations = 0
        while iterations < self._max_iterations:
            result = self._executor.run(user_message)
            for msg in result.user_messages:
                on_message(msg)
                if msg.role == "assistant" and msg.content.strip():
                    last_content = msg.content
            if result.task_completed:
                return last_content
            if result.error is not None and result.error.code == LLM_ALL_PROVIDERS_FAILED:
                raise result.error
            iterations += 1
            user_message = None
        return last_content
