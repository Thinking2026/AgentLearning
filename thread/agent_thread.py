from __future__ import annotations

import threading
from typing import Any

from config import JsonConfig
from context.shared_context import SharedContext
from llm import (
    BaseLLMClient,
    DeepSeekLLMClient,
    DynamicLLMClient,
    LLMProviderRegistry,
    OpenAILLMClient,
    QwenLLMClient,
)
from llm.message_formatter import MessageFormatter
from queue.message_queue import MessageQueue
from rag.rag_service import RAGService
from rag.storage import ChromaDBStorage, FileStorage, SQLiteStorage, StorageRegistry
from schemas import AgentError, ChatMessage, LLMRequest, LLMResponse, SessionStatus, build_error
from tools import ToolRegistry, create_default_tool_registry


class AgentThread(threading.Thread):
    def __init__(
        self,
        message_queue: MessageQueue,
        shared_context: SharedContext,
        config: JsonConfig,
        max_tool_iterations: int | None = None,
    ) -> None:
        super().__init__(name="AgentThread", daemon=True)
        self._message_queue = message_queue
        self._shared_context = shared_context
        self._config = config
        self._storage_registry = self._build_storage_registry()
        self._storage = self._build_storage()
        self._rag_service = self._build_rag_service()
        self._message_formatter = self._build_message_formatter()
        self._tool_registry = self._build_tool_registry()
        self._llm_client = self._build_llm_client()
        self._base_system_prompt = self._shared_context.get_system_prompt()
        self._max_tool_iterations = max_tool_iterations or int(
            self._config.get("agent.max_tool_iterations", 3)
        )
        self._max_react_attempt_iterations = int(
            self._config.get("agent.max_react_attempt_iterations", 20)
        )
        self._cur_react_attempt_iterations = 0
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def cleanup(self) -> None:
        self._cur_react_attempt_iterations = 0
        self._shared_context.set_session_status(SessionStatus.NEW_TASK)
        self._restore_base_system_prompt()

    def _build_storage_registry(self) -> StorageRegistry:
        file_storage = FileStorage(
            self._config.get("storage.file.path", "runtime/agent_documents.json")
        )
        sqlite_path = self._config.get("storage.sqlite.path", "runtime/agent_storage.db")
        sqlite_storage = SQLiteStorage(sqlite_path)
        sqlite_storage.seed(file_storage.get_documents())
        storages = [file_storage, sqlite_storage]

        chromadb_path = self._config.get("storage.chromadb.persist_directory")
        if chromadb_path:
            chromadb_storage = ChromaDBStorage(
                persist_directory=chromadb_path,
                collection_name=self._config.get("storage.chromadb.collection_name", "agent_documents"),
            )
            if not chromadb_storage.get_documents():
                chromadb_storage.upsert_documents(file_storage.get_documents())
            storages.append(chromadb_storage)

        return StorageRegistry(storages)

    def _build_storage(self):
        backend_name = self._config.get("storage.backend", "file")
        return self._storage_registry.get(backend_name)

    def _build_rag_service(self) -> RAGService:
        return RAGService(self._storage)

    @staticmethod
    def _build_message_formatter() -> MessageFormatter:
        return MessageFormatter()

    def _build_tool_registry(self) -> ToolRegistry:
        package_name = self._config.get("tools.package")
        module_names = self._config.get("tools.modules", [])
        if not isinstance(module_names, list):
            module_names = []
        return create_default_tool_registry(
            module_names=module_names,
            package_name=package_name,
        )

    def _build_llm_client(self) -> BaseLLMClient:
        registry = LLMProviderRegistry()
        provider_name = self._config.get("llm.provider", "openai")
        if provider_name == "openai":
            provider = OpenAILLMClient.from_settings(
                api_key=self._config.get("llm.api_key"),
                model=self._config.get("llm.model", "gpt-4.1-mini"),
                base_url=self._config.get("llm.base_url", "https://api.openai.com/v1"),
                timeout=float(self._config.get("llm.timeout", 60.0)),
            )
            registry.register(provider)
        elif provider_name == "qwen":
            provider = QwenLLMClient.from_settings(
                api_key=self._config.get("llm.api_key"),
                model=self._config.get("llm.model", "qwen-plus"),
                base_url=self._config.get("llm.base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout=float(self._config.get("llm.timeout", 60.0)),
            )
            registry.register(provider)
        elif provider_name == "deepseek":
            provider = DeepSeekLLMClient.from_settings(
                api_key=self._config.get("llm.api_key"),
                model=self._config.get("llm.model", "deepseek-chat"),
                base_url=self._config.get("llm.base_url", "https://api.deepseek.com/v1"),
                timeout=float(self._config.get("llm.timeout", 60.0)),
            )
            registry.register(provider)
        else:
            raise build_error("LLM_PROVIDER_NOT_FOUND", f"Unsupported LLM provider: {provider_name}")
        return DynamicLLMClient(registry, default_provider=provider_name)

    def _generate_react_prompt(self, user_message: ChatMessage) -> str:
        return (
            "Start a new reasoning loop for this user request.\n"
            f"User question: {user_message.content}"
        )

    def run(self) -> None:
        while not self._stop_event.is_set() and not self._message_queue.is_closed():
            session_status = self._shared_context.get_session_status()

            if (
                session_status == SessionStatus.IN_PROGRESS
                and self._cur_react_attempt_iterations > self._max_react_attempt_iterations
            ):
                self._message_queue.send_agent_message(
                    ChatMessage(
                        role="system",
                        content="Sorry, this question is too hard, i can not solve",
                    )
                )
                self.cleanup()
                continue

            user_message = self._wait_for_user_message(session_status)#TODO user_thread增加一种控制消息，用于用户退出后清理所有对象
            if user_message.metadata.get("control") == "shutdown":
                self._message_queue.close()
                self.stop()
                break

            try:
                prompt = self._build_current_prompt(session_status, user_message)
                request = self._format_llm_request(prompt, user_message)
                self._shared_context.append_system_prompt_line(prompt)
                llm_response = self._call_llm_with_timeout_handling(request)
                parsed_response = self._parse_llm_api_response(llm_response)
                if parsed_response is None:
                    self._handle_unexpected_llm_response(llm_response)
                    continue
                if self._route_llm_response(parsed_response):
                    continue
            except Exception as exc:
                agent_error = self._normalize_error(exc)
                error_message = ChatMessage(
                    role="assistant",
                    content=str(agent_error),
                    metadata={"error_code": agent_error.code, "error_message": agent_error.message},
                )
                self._message_queue.send_agent_message(error_message)
                self.cleanup()

    def _wait_for_user_message(self, session_status: SessionStatus) -> ChatMessage:
        timeout = None if session_status == SessionStatus.NEW_TASK else 5.0
        while not self._stop_event.is_set() and not self._message_queue.is_closed():
            user_message = self._message_queue.get_user_message(timeout=timeout)
            if user_message is not None:
                return user_message
            if session_status == SessionStatus.IN_PROGRESS:
                return ChatMessage(role="system", content="", metadata={"control": "poll_timeout"})
        return ChatMessage(role="system", content="shutdown", metadata={"control": "shutdown"})

    def _build_current_prompt(
        self,
        session_status: SessionStatus,
        user_message: ChatMessage,
    ) -> str:
        if session_status == SessionStatus.NEW_TASK:
            self._shared_context.set_session_status(SessionStatus.IN_PROGRESS)
            self._cur_react_attempt_iterations = 0
            return self._generate_react_prompt(user_message)
        if user_message.content.strip():
            return user_message.content.strip()
        return self._build_continuation_prompt()

    def _build_continuation_prompt(self) -> str:
        return "Continue reasoning from the existing prompt context and decide the next best action."

    def _format_llm_request(
        self,
        prompt: str,
        user_message: ChatMessage,
    ) -> LLMRequest:
        rag_context = self._rag_service.retrieve(user_message.content) if user_message.content.strip() else []
        conversation = [ChatMessage(role="user", content=prompt)]
        return self._message_formatter.build_request(
            system_prompt=self._shared_context.get_system_prompt(),
            conversation=conversation,
            tools=self._tool_registry.get_tool_schemas(),
            context=rag_context,
        )

    def _call_llm_with_timeout_handling(self, request: LLMRequest) -> LLMResponse:
        try:
            return self._llm_client.generate(request)
        except TimeoutError as exc:
            self._handle_llm_timeout(exc)
            raise
        except AgentError as exc:
            if exc.code == "LLM_TIMEOUT":
                self._handle_llm_timeout(exc)
            raise

    def _handle_llm_timeout(self, exc: Exception) -> None:
        timeout_message = ChatMessage(
            role="system",
            content=f"LLM call timed out. Temporary timeout strategy applied: {exc}",
        )
        self._message_queue.send_agent_message(timeout_message)
        self.cleanup()

    def _parse_llm_api_response(self, response: Any) -> LLMResponse | None:
        try:
            if response is None:
                return None
            if not isinstance(response, LLMResponse):
                return None
            return self._message_formatter.parse_response(response)
        except Exception:
            return None

    def _handle_unexpected_llm_response(self, response: Any) -> None:
        fallback = ChatMessage(
            role="system",
            content=f"LLM returned an unexpected response format: {response}",
        )
        self._message_queue.send_agent_message(fallback)
        self.cleanup()

    def _route_llm_response(self, response: LLMResponse) -> bool:
        if response.tool_calls:
            self._handle_tool_calls(response)
            return True

        external_query = self._extract_external_query(response)
        if external_query:
            self._handle_external_lookup(external_query)
            return True

        self._message_queue.send_agent_message(self._format_final_conclusion(response))
        self.cleanup()
        return True

    def _format_final_conclusion(self, response: LLMResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.assistant_message.content,
            metadata=response.assistant_message.metadata,
        )
        return True

    def _handle_tool_calls(self, response: LLMResponse) -> None:
        if self._cur_react_attempt_iterations >= self._max_tool_iterations:
            self._message_queue.send_agent_message(
                ChatMessage(
                    role="assistant",
                    content="工具调用次数超过上限，本轮先停止，避免进入死循环。",
                )
            )
            self.cleanup()
            return

        for tool_call in response.tool_calls:
            result = self._tool_registry.execute(
                tool_call.name,
                tool_call.arguments,
                tool_call.call_id,
            )
            observation_text = (
                result.output
                if result.success
                else f"Tool error: {result.error if result.error else 'unknown error'}"
            )
            observation = self._message_formatter.format_tool_observation(
                tool_name=tool_call.name,
                output=observation_text,
                call_id=tool_call.call_id,
            )
            self._shared_context.append_system_prompt_line(observation.content)
        self._cur_react_attempt_iterations += 1

    def _extract_external_query(self, response) -> str | None:
        metadata_query = response.assistant_message.metadata.get("external_query")
        if isinstance(metadata_query, str) and metadata_query.strip():
            return metadata_query.strip()

        content = response.assistant_message.content.strip()
        prefix = "RAG_QUERY:"
        if content.startswith(prefix):
            query = content[len(prefix):].strip()
            return query or None
        return None

    def _handle_external_lookup(self, query: str) -> None:
        rag_context = self._rag_service.retrieve(query)
        formatted_context = self._message_formatter.build_system_prompt(
            "External lookup result:",
            rag_context,
        )
        self._shared_context.append_system_prompt_line(formatted_context)
        self._cur_react_attempt_iterations += 1

    def _restore_base_system_prompt(self) -> None:
        with self._shared_context._lock:
            self._shared_context._system_prompt = self._base_system_prompt

    @staticmethod
    def _normalize_error(exc: Exception) -> AgentError:
        if isinstance(exc, AgentError):
            return exc
        return build_error("UNEXPECTED_ERROR", f"Agent encountered an unexpected error: {exc}")
