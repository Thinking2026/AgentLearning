from __future__ import annotations

import threading
from typing import Callable
from uuid import uuid4

from config import ConfigValueReader, JsonConfig
from agent.factory.agent_factory import AgentFactory
from agent.application.pipeline import Pipeline
from schemas.ids import TaskId
from utils.concurrency.message_queue import AgentToUserQueue, UserToAgentQueue
from schemas import (
    AGENT_THREAD_ERROR,
    AgentError,
    UIMessage,
    LLM_ALL_PROVIDERS_FAILED,
    build_error,
)
from infra.observability.tracing import Span, Tracer
from utils.log.log import Logger, zap
from utils.concurrency.thread_event import ThreadEvent


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
        self._tracer: Tracer | None = None
        self._session_span: Span | None = None
        self._factory: AgentFactory | None = None
        self._pipeline: Pipeline | None = None
        self._load_tracing_config()
        try:
            self._tracer = self._build_tracer()
            self._factory = AgentFactory.from_config(config, self._tracer)
        except Exception as exc:
            self._logger.error(
                "AgentThread init failed",
                zap.any("error", str(exc)),
            )
            raise

    def run(self) -> None:
        try:
            while self._is_running():
                user_message = self._user_to_agent_queue.get_user_message(timeout=None)
                if not self._is_running():
                    break
                if user_message is None:
                    continue

                self._record_user_input_trace(user_message)
                self._start_session_trace(user_message)

                task_id = TaskId(f"task_{uuid4().hex}")
                task_description = user_message.content.strip()

                # Build a fresh Pipeline for each task
                try:
                    pipeline = self._factory.build_pipeline(task_id, task_description)
                except Exception as exc:
                    self._logger.error("Pipeline build failed", zap.any("error", str(exc)))
                    self._finish_session_trace(error=self._normalize_error(exc))
                    self._send_task_completed()
                    continue

                try:
                    result = pipeline.run(
                        task_id=task_id,
                        task_description=task_description,
                    )
                    if result.succeeded and result.result:
                        self._agent_to_user_queue.send_agent_message(UIMessage(
                            role="assistant",
                            content=result.result,
                            metadata={"source": "task_result"},
                        ))
                    elif result.error_reason:
                        self._agent_to_user_queue.send_agent_message(UIMessage(
                            role="assistant",
                            content=result.error_reason,
                            metadata={"source": "error"},
                        ))
                    self._finish_session_trace(error=None)
                except Exception as exc:
                    normalized = self._normalize_error(exc)
                    self._logger.error("Task execution failed", zap.any("error", normalized))
                    self._finish_session_trace(error=normalized)
                    if self._is_hard_error(normalized):
                        break
                finally:
                    self._send_task_completed()

        except Exception as exc:
            self._logger.error("Agent thread crashed", zap.any("error", exc))
        finally:
            self._stop()

    # ------------------------------------------------------------------
    # Tracing helpers
    # ------------------------------------------------------------------

    def _load_tracing_config(self) -> None:
        self._tracing_enabled = bool(self._config.get("tracing.enabled", True))
        self._tracing_output_path = self._config.get("tracing.output_path", "var/tracing/traces.jsonl")
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

    def _start_session_trace(self, user_message: UIMessage) -> None:
        if self._tracer is None or self._session_span is not None:
            return
        self._session_span = self._tracer.start_trace(
            "session",
            attributes={"thread": self.name, "task": user_message.content},
        )

    def _finish_session_trace(self, error: Exception | AgentError | None = None) -> None:
        if self._session_span is None:
            return
        status = "error" if error is not None else "ok"
        self._session_span.finish(status=status, error=error)
        self._session_span = None

    def _record_user_input_trace(self, user_message: UIMessage) -> None:
        if self._tracer is None:
            return
        with self._tracer.start_span(
            name="user.input",
            type="input",
            attributes={"role": user_message.role, "content": user_message.content},
        ):
            return

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def _send_task_completed(self) -> None:
        self._agent_to_user_queue.send_agent_message(UIMessage(
            role="assistant",
            content="",
            metadata={"control": True, "task_completed": True},
        ))

    def _stop(self) -> None:
        self._stop_callback(self.name)

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
