from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config.config import JsonConfig
from config.reader import ConfigValueReader
from infra.db.bootstrap_documents import load_seed_documents
from infra.db.impl.chromadb_storage import ChromaDBStorage
from infra.db.impl.mysql_storage import MySQLStorage
from infra.db.impl.sqlite_storage import SQLiteStorage
from infra.db.registry import StorageRegistry
from infra.eventbus.event_bus import InMemoryEventBus
from llm.llm_gateway import LLMGateway
from llm.registry import LLMProviderRegistry
from llm.providers.claude_api import ClaudeLLMClient
from llm.providers.deepseek_api import DeepSeekLLMClient
from llm.providers.glm_api import GLMLLMClient
from llm.providers.kimi_api import KimiLLMClient
from llm.providers.minmax_api import MinMaxLLMClient
from llm.providers.openai_api import OpenAILLMClient
from llm.providers.qwen_api import QwenLLMClient
from schemas.errors import LLM_PROVIDER_NOT_FOUND, STORAGE_CONFIG_ERROR, build_error
from schemas.ids import TaskId
from tools import create_default_tool_registry
from tools.impl.sql_query_tool import SQLQueryTool, build_sql_query_tool_name, build_sql_query_tool_description
from tools.impl.sql_schema_tool import SQLSchemaTool, build_sql_schema_tool_name, build_sql_schema_tool_description
from tools.impl.vector_search_tool import VectorSearchTool, build_vector_search_tool_name, build_vector_search_tool_description
from tools.impl.vector_schema_tool import VectorSchemaTool, build_vector_schema_tool_name, build_vector_schema_tool_description
from utils.log.log import Logger

from agent.application.pipeline import Pipeline
from agent.models.checkpoint.checkpoint_processor import CheckpointProcessor
from agent.models.context.manager import ContextManager
from agent.models.evaluate.quality_evaluator import QualityEvaluator
from agent.models.executor.stage_executor import StageExecutor
from agent.models.knowledge.knowledge_loader import KnowledgeLoader
from agent.models.knowledge.knowledge_manager import KnowledgeManager
from agent.models.model_routing.provider_router import ModelSelector
from agent.models.plan.planner import Planner
from agent.models.reasoning.impl.react.react_strategy import ReActStrategy
from agent.models.reasoning.reasoning_manager import ReasoningManager

if TYPE_CHECKING:
    from infra.observability.tracing import Tracer


class AgentFactory:
    """Builds a fully-wired Pipeline from AgentConfig.

    Single assembly point: domain objects do not know about config format.
    """

    def __init__(self, config: JsonConfig, tracer: Tracer | None = None) -> None:
        self._config = config
        self._reader = ConfigValueReader(config)
        self._tracer = tracer
        self._logger = Logger.get_instance()

    @classmethod
    def from_config(cls, config: JsonConfig, tracer: Tracer | None = None) -> AgentFactory:
        return cls(config, tracer)

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------

    def build_event_bus(self) -> InMemoryEventBus:
        return InMemoryEventBus()

    def build_storage_registry(self) -> StorageRegistry:
        seed_documents = load_seed_documents(
            self._config.get("storage.file.path", "tests/runtime/nanoagent_soul.json")
        )
        storages = [SQLiteStorage(self._build_sqlite_databases())]

        chromadb_path = self._config.get("storage.chromadb.persist_directory")
        if chromadb_path:
            collections = self._build_chromadb_collections()
            chromadb = ChromaDBStorage(
                persist_directory=chromadb_path,
                collections=collections,
            )
            bootstrap = self._config.get("storage.chromadb.bootstrap_collection")
            if isinstance(bootstrap, str) and bootstrap.strip():
                if not chromadb.get_documents(bootstrap):
                    chromadb.upsert_documents(bootstrap, seed_documents)
            storages.append(chromadb)

        mysql_host = str(self._config.get("storage.mysql.host", "")).strip()
        if mysql_host:
            storages.append(MySQLStorage(
                host=mysql_host,
                port=int(self._config.get("storage.mysql.port", 3306)),
                user=os.getenv("MYSQL_USER", ""),
                password=os.getenv("MYSQL_PASSWORD", ""),
                allowed_databases=self._build_mysql_databases(),
                charset=str(self._config.get("storage.mysql.charset", "utf8mb4")),
            ))

        return StorageRegistry(storages)

    def build_llm_provider_registry(self) -> LLMProviderRegistry:
        priority_chain = self._config.get("llm.priority_chain", ["deepseek"])
        if not isinstance(priority_chain, list) or not priority_chain:
            priority_chain = ["deepseek"]
        registry = LLMProviderRegistry()
        for name in priority_chain:
            registry.register(self._build_provider(name))
        return registry

    def build_tool_registry(self, storage_registry: StorageRegistry | None = None):
        package_name = self._config.get("tools.package", "tools.impl")
        if not isinstance(package_name, str) or not package_name.strip():
            package_name = "tools.impl"
        module_names = self._config.get("tools.modules", [])
        if not isinstance(module_names, list):
            module_names = []
        registry = create_default_tool_registry(
            module_names=module_names,
            package_name=package_name,
            timeout_retry_max_attempts=int(self._config.get("tools.retry.max_attempts", 4)),
            timeout_retry_delays=self._reader.retry_delays("tools.retry.backoff_seconds"),
            tracer=self._tracer,
            logger=self._logger,
        )
        if storage_registry:
            self._register_storage_tools(registry, storage_registry)
        return registry

    # ------------------------------------------------------------------
    # LLM gateway
    # ------------------------------------------------------------------

    def build_llm_gateway(self, provider_name: str) -> LLMGateway:
        provider = self._build_provider(provider_name)
        return LLMGateway(
            provider=provider,
            max_retries=int(self._config.get("llm.retry.max_attempts", 3)),
            retry_delays=self._reader.retry_delays("llm.retry.backoff_seconds") or (1.0, 2.0, 4.0),
            timeout=float(self._config.get(f"llm.provider_settings.{provider_name}.timeout", 60.0)),
        )

    # ------------------------------------------------------------------
    # Domain objects
    # ------------------------------------------------------------------

    def build_model_selector(self) -> ModelSelector:
        priority_chain = self._config.get("llm.priority_chain", ["deepseek"])
        if not isinstance(priority_chain, list) or not priority_chain:
            priority_chain = ["deepseek"]
        return ModelSelector(
            priority_chain=priority_chain,
            enable_fallback=bool(self._config.get("llm.enable_provider_fallback", False)),
        )

    def build_context_manager(self) -> ContextManager:
        return ContextManager()

    def build_knowledge_loader(
        self,
        knowledge_manager: KnowledgeManager | None = None,
    ) -> KnowledgeLoader:
        return KnowledgeLoader(knowledge_manager)

    def build_reasoning_manager(self, provider_name: str) -> ReasoningManager:
        gateway = self.build_llm_gateway(provider_name)
        strategy = ReActStrategy()
        return ReasoningManager(llm_gateway=gateway, strategy=strategy)

    def build_stage_executor(
        self,
        provider_name: str,
        quality_evaluator: QualityEvaluator,
        knowledge_loader: KnowledgeLoader,
    ) -> StageExecutor:
        return StageExecutor(
            reasoning_manager=self.build_reasoning_manager(provider_name),
            context_manager=self.build_context_manager(),
            tool_registry=self.build_tool_registry(),
            quality_evaluator=quality_evaluator,
            knowledge_loader=knowledge_loader,
            max_iterations=int(self._config.get("agent.max_attempt_iterations", 60)),
        )

    def build_planner(
        self,
        task_id: TaskId,
        task_description: str,
        knowledge_loader: KnowledgeLoader | None = None,
    ) -> Planner:
        primary = self._primary_provider_name()
        gateway = self.build_llm_gateway(primary)
        return Planner.create(
            task_id=task_id,
            task_description=task_description,
            llm_gateway=gateway,
            knowledge_loader=knowledge_loader,
        )

    def build_quality_evaluator(
        self,
        task_id: TaskId,
        task_description: str,
    ) -> QualityEvaluator:
        primary = self._primary_provider_name()
        gateway = self.build_llm_gateway(primary)
        return QualityEvaluator.for_task(
            task_id=task_id,
            task_description=task_description,
            llm_gateway=gateway,
        )

    def build_knowledge_manager(self, task_id: TaskId) -> KnowledgeManager:
        primary = self._primary_provider_name()
        gateway = self.build_llm_gateway(primary)
        storage_registry = self.build_storage_registry()
        vector_storage = None
        if "chromadb" in storage_registry.list_backends():
            vector_storage = storage_registry.get("chromadb")
        return KnowledgeManager.for_task(
            task_id=task_id,
            llm_gateway=gateway,
            vector_storage=vector_storage,
        )

    def build_checkpoint_processor(self, task_id: TaskId) -> CheckpointProcessor:
        return CheckpointProcessor.create_for_task(task_id)

    # ------------------------------------------------------------------
    # Top-level entry point
    # ------------------------------------------------------------------

    def build_pipeline(self, task_id: TaskId, task_description: str) -> Pipeline:
        """Build a fully-wired Pipeline for a single task."""
        primary = self._primary_provider_name()
        event_bus = self.build_event_bus()
        llm_registry = self.build_llm_provider_registry()
        model_selector = self.build_model_selector()
        quality_evaluator = self.build_quality_evaluator(task_id, task_description)
        knowledge_manager = self.build_knowledge_manager(task_id)
        knowledge_loader = self.build_knowledge_loader(knowledge_manager)
        planner = self.build_planner(task_id, task_description, knowledge_loader)
        stage_executor = self.build_stage_executor(primary, quality_evaluator, knowledge_loader)
        checkpoint_processor = self.build_checkpoint_processor(task_id)

        return Pipeline(
            planner=planner,
            stage_executor=stage_executor,
            checkpoint_processor=checkpoint_processor,
            knowledge_manager=knowledge_manager,
            quality_evaluator=quality_evaluator,
            model_selector=model_selector,
            llm_provider_registry=llm_registry,
            event_bus=event_bus,
            max_plan_retries=int(self._config.get("agent.max_plan_retries", 3)),
            max_stage_retries=int(self._config.get("agent.max_stage_retries", 2)),
            max_quality_retries=int(self._config.get("agent.max_quality_retries", 2)),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _primary_provider_name(self) -> str:
        chain = self._config.get("llm.priority_chain", ["deepseek"])
        if isinstance(chain, list) and chain:
            return str(chain[0])
        return "deepseek"

    def _build_provider(self, provider_name: str):
        settings = self._config.get(f"llm.provider_settings.{provider_name}", {})
        if not isinstance(settings, dict):
            settings = {}
        timeout = float(settings.get("timeout", 60.0))
        api_key = settings.get("api_key")

        if provider_name == "openai":
            return OpenAILLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "gpt-4o-mini"),
                base_url=settings.get("base_url", "https://api.openai.com/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "qwen":
            return QwenLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "qwen-plus"),
                base_url=settings.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "deepseek":
            return DeepSeekLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "deepseek-chat"),
                base_url=settings.get("base_url", "https://api.deepseek.com/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "claude":
            return ClaudeLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "claude-3-5-sonnet-latest"),
                base_url=settings.get("base_url", "https://api.anthropic.com"),
                timeout=timeout,
                max_tokens=int(settings.get("max_tokens", self._config.get("llm.max_tokens", 1024))),
                anthropic_version=settings.get(
                    "anthropic_version",
                    self._config.get("llm.anthropic_version", "2023-06-01"),
                ),
            ).set_tracer(self._tracer)
        if provider_name == "minmax":
            return MinMaxLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "MiniMax-Text-01"),
                base_url=settings.get("base_url", "https://api.minimax.chat/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "glm":
            return GLMLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "glm-4"),
                base_url=settings.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        if provider_name == "kimi":
            return KimiLLMClient.from_settings(
                api_key=api_key,
                model=settings.get("model", "moonshot-v1-8k"),
                base_url=settings.get("base_url", "https://api.moonshot.cn/v1"),
                timeout=timeout,
            ).set_tracer(self._tracer)
        raise build_error(LLM_PROVIDER_NOT_FOUND, f"Unsupported LLM provider: {provider_name}")

    def _build_sqlite_databases(self) -> dict[str, str]:
        sqlite_config = self._config.get("storage.sqlite", {})
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
            databases.setdefault(self._derive_sqlite_alias(fallback_path), fallback_path)
        if not databases:
            databases["local_storage"] = "var/storage/nanoagent_local_storage.db"
        return databases

    def _build_mysql_databases(self) -> list[str]:
        mysql_config = self._config.get("storage.mysql", {})
        if not isinstance(mysql_config, dict):
            mysql_config = {}
        configured = mysql_config.get("allowed_databases")
        if isinstance(configured, list):
            dbs = [str(d).strip() for d in configured if str(d).strip()]
            if dbs:
                return dbs
        fallback = str(mysql_config.get("database", "")).strip()
        if fallback:
            return [fallback]
        raise build_error(
            STORAGE_CONFIG_ERROR,
            "MySQL storage requires `storage.mysql.allowed_databases` or `storage.mysql.database`.",
        )

    def _build_chromadb_collections(self) -> list[str]:
        chromadb_config = self._config.get("storage.chromadb", {})
        if not isinstance(chromadb_config, dict):
            chromadb_config = {}
        configured = chromadb_config.get("allowed_collections") or chromadb_config.get("collections")
        if isinstance(configured, list):
            cols = [str(c).strip() for c in configured if str(c).strip()]
            if cols:
                return cols
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

    def _register_storage_tools(self, tool_registry, storage_registry: StorageRegistry) -> None:
        for backend_name in storage_registry.list_backends():
            storage = storage_registry.get(backend_name)
            if backend_name in {"sqlite", "mysql"}:
                resources = ", ".join(storage.list_resources()) or "<none>"
                tool_registry.register(SQLSchemaTool(
                    name=build_sql_schema_tool_name(backend_name),
                    description=build_sql_schema_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
                tool_registry.register(SQLQueryTool(
                    name=build_sql_query_tool_name(backend_name),
                    description=build_sql_query_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
            elif backend_name == "chromadb":
                resources = ", ".join(storage.list_resources()) or "<none>"
                tool_registry.register(VectorSchemaTool(
                    name=build_vector_schema_tool_name(backend_name),
                    description=build_vector_schema_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
                tool_registry.register(VectorSearchTool(
                    name=build_vector_search_tool_name(backend_name),
                    description=build_vector_search_tool_description(backend_name, resources),
                    storage=storage,
                    backend_name=backend_name,
                ))
