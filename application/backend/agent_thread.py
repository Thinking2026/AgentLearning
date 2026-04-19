from __future__ import annotations

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
    SessionStatus,
    build_error,
)
from tracing import Span, Tracer
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
        self._executor: AgentExecutor | None = None
        self._tracer: Tracer | None = None
        self._session_span: Span | None = None
        self._load_tracing_config()
        try:
            self._tracer = self._build_tracer()
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
        self._executor = None
        self._session_span = None

    def _build_executor(self) -> AgentExecutor:
        return AgentExecutor(
            session=self._session,
            config=self._config,
            tracer=self._tracer,
            logger=self._logger,
        )

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
