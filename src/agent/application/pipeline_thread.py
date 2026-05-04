from __future__ import annotations

import threading
from typing import Callable
from uuid import uuid4

from config import ConfigValueReader, JsonConfig
from agent.factory.agent_factory import AgentFactory
from agent.application.driver import PipelineDriver
from schemas.ids import TaskId
from schemas.errors import AgentError, build_error, AGENT_THREAD_ERROR, LLM_ALL_PROVIDERS_FAILED
from schemas.types import UserMessage
from infra.observability.tracing import Span, Tracer
from utils.log.log import Logger, zap
from utils.concurrency.message_queue import UserMessageQueue, TaskQueue, AgentMessageQueue
from utils.concurrency.thread_event import ThreadEvent


class PipelineThread(threading.Thread):
    def __init__(
        self,
        agent_msg_queue: AgentMessageQueue,
        task_queue: TaskQueue,
        user_msg_queue: UserMessageQueue,
        config: JsonConfig,
        stop_event: ThreadEvent,
        stop_callback: Callable[[str | None], None],
        logger: Logger,
    ) -> None:
        super().__init__(name="PipelineThread", daemon=False)
        # PipelineThread owns its inbound queue
        self._task_queue = task_queue
        self._agent_msg_queue = agent_msg_queue
        self._user_msg_queue = user_msg_queue
        self._config = config
        self._config_value_reader = ConfigValueReader(config)
        self._stop_event = stop_event
        self._stop_callback = stop_callback
        self._logger = logger
        self._tracer: Tracer | None = None
        self._session_span: Span | None = None
        self._factory: AgentFactory | None = None
        self._active_driver: PipelineDriver | None = None
        self._load_tracing_config()
        try:
            self._tracer = self._build_tracer()
            self._factory = AgentFactory.from_config(config, self._tracer)
        except Exception as exc:
            self._logger.error("PipelineThread init failed", zap.any("error", str(exc)))
            raise

    def loop_user_message(self, timeout: float) -> UserMessage:
        return self._agent_msg_queue.get(timeout=timeout)

    def publish_event(self, msg: UserMessage) -> None:
        self._user_msg_queue.send(msg)
    
    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            while self._is_running():
                # Wait for the next task from the user
                user_message = self._task_queue.get_user_message(timeout=None)
                if not self._is_running():
                    break
                if user_message is None:
                    continue

                self._record_user_input_trace(user_message)
                self._start_session_trace(user_message)

                task_id = TaskId(f"task_{uuid4().hex}")
                task_description = user_message.content.strip()

                # Build a fresh Pipeline + Driver for each task
                try:
                    pipeline = self._factory.build_pipeline(task_id, task_description)
                except Exception as exc:
                    self._logger.error("Pipeline build failed", zap.any("error", str(exc)))
                    self._finish_session_trace(error=self._normalize_error(exc))
                    self._send_task_completed()
                    continue

                driver = PipelineDriver(
                    pipeline=pipeline,
                    send_to_user=self._agent_to_user_queue.send_agent_message,
                    logger=self._logger,
                )
                self._active_driver = driver

                # Run the pipeline in a sub-thread so this thread can keep
                # forwarding user messages (cancel / guidance / clarification).
                result_holder: list = [None]
                error_holder: list = [None]

                def _run_pipeline() -> None:
                    try:
                        result_holder[0] = driver.submit_task(task_id, task_description)
                    except Exception as exc:
                        error_holder[0] = exc

                pipeline_thread = threading.Thread(
                    target=_run_pipeline, name="PipelineWorker", daemon=True
                )
                pipeline_thread.start()

                # Forward user messages while the pipeline is running
                while pipeline_thread.is_alive():
                    msg = self._task_queue.get_user_message(timeout=0.5)
                    if msg is not None:
                        driver.route_user_message(msg)

                pipeline_thread.join()
                self._active_driver = None

                # Handle result
                if error_holder[0] is not None:
                    normalized = self._normalize_error(error_holder[0])
                    self._logger.error("Task execution failed", zap.any("error", normalized))
                    self._finish_session_trace(error=normalized)
                    if self._is_hard_error(normalized):
                        break
                else:
                    result = result_holder[0]
                    if result is not None:
                        if result.succeeded and result.result:
                            self._agent_to_user_queue.send_agent_message(ClientMessage(
                                role="assistant",
                                content=result.result,
                                metadata={"source": "task_result"},
                            ))
                        elif result.error_reason:
                            self._agent_to_user_queue.send_agent_message(ClientMessage(
                                role="assistant",
                                content=result.error_reason,
                                metadata={"source": "error"},
                            ))
                    self._finish_session_trace(error=None)

                self._send_task_completed()

        except Exception as exc:
            self._logger.error("PipelineThread crashed", zap.any("error", exc))
        finally:
            self._stop()

    # ------------------------------------------------------------------
    # Tracing helpers
    # ------------------------------------------------------------------

    def _load_tracing_config(self) -> None:
        self._tracing_enabled = bool(self._config.get("tracing.enabled", True))
        self._tracing_output_path = self._config.get(
            "tracing.output_path", "var/tracing/traces.jsonl"
        )
        self._tracing_payload_redaction_enabled = bool(
            self._config.get(
                "tracing.payload_redaction_enabled",
                not bool(self._config.get("tracing.capture_payloads", False)),
            )
        )
        self._tracing_max_content_length = self._config_value_reader.positive_int(
            "tracing.max_content_length", default=1000
        )

    def _build_tracer(self) -> Tracer:
        return Tracer(
            enabled=self._tracing_enabled,
            output_path=self._tracing_output_path,
            payload_redaction_enabled=self._tracing_payload_redaction_enabled,
            max_content_length=self._tracing_max_content_length,
        )

    def _start_session_trace(self, user_message: ClientMessage) -> None:
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

    def _record_user_input_trace(self, user_message: ClientMessage) -> None:
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
        self._agent_to_user_queue.send_agent_message(ClientMessage(
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
        return (
            self._task_queue.is_closed()
            or self._agent_to_user_queue.is_closed()
        )
