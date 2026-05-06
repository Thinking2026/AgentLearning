from __future__ import annotations

import threading
from typing import Callable
from uuid import uuid4

from config import ConfigReader
from agent.factory.agent_factory import AgentFactory
from agent.application.driver import PipelineDriver
from schemas.ids import TaskId
from schemas.errors import AgentError, build_error, AGENT_THREAD_ERROR
from schemas.task import TaskResult
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
        config: ConfigReader,
        stop_event: ThreadEvent,
        stop_callback: Callable[[str | None], None],
    ) -> None:
        super().__init__(name="PipelineThread", daemon=False)
        self._task_queue = task_queue
        self._agent_msg_queue = agent_msg_queue
        self._user_msg_queue = user_msg_queue
        self._config = config
        self._stop_event = stop_event
        self._stop_callback = stop_callback
        self._session_span: Span | None = None
        self._factory: AgentFactory | None = None
        self._active_driver: PipelineDriver | None = None
        try:
            self._load_tracing_config()
            self._logger = Logger.get_instance()
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
            self._active_driver = self._factory.build_pipeline_driver()
            pipeline = self._factory.build_pipeline(task_id, self._active_driver)
            pipeline.stage_executor.set_driver(self._active_driver)
            self._active_driver.set_pipeline(pipeline)
            self._active_driver.set_thread(self)

            while self._is_running():
                # Wait for the next task from the user
                user_message = self._task_queue.get(timeout=None)
                if not self._is_running():
                    self._logger.info("PipelineThread stopping, exiting loop")
                    break
                if user_message is None:
                    self._logger.info("PipelineThread received shutdown signal, exiting loop")
                    break

                self._record_user_input_trace(user_message)
                self._start_session_trace(user_message)

                task_id = TaskId(f"task_{uuid4().hex}")
                task_description = user_message.content.strip()

                # Build a fresh Pipeline + Driver for each task
                result = self._active_driver.submit_task(task_id, task_description)
                if not result.succeeded:
                    self._logger.error(
                        "Task execution failed",
                        zap.any("task_id", task_id),
                        zap.any("error", result.error),
                    )
                self._send_task_completed(result)
                self._finish_session_trace(error=build_error(AGENT_THREAD_ERROR, str(result.error)) if result.error else None)

        except Exception as exc:
            self._logger.error("PipelineThread crashed", zap.any("error", exc))
        finally:
            self._stop()

    def _send_task_completed(self, result: TaskResult) -> None:
        msg = UserMessage(
            content=result.output,
            metadata={"succeeded": result.succeeded, "error": result.error},
        )
        self.publish_event(msg)

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
        self._tracing_max_content_length = self._config.positive_int(
            "tracing.max_content_length", default=1000
        )

    def _build_tracer(self) -> Tracer:
        return Tracer(
            enabled=self._tracing_enabled,
            output_path=self._tracing_output_path,
            payload_redaction_enabled=self._tracing_payload_redaction_enabled,
            max_content_length=self._tracing_max_content_length,
        )

    def _start_session_trace(self, user_message: UserMessage) -> None:
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

    def _record_user_input_trace(self, user_message: UserMessage) -> None:
        if self._tracer is None:
            return
        with self._tracer.start_span(
            name="user.input",
            type="input",
            attributes={"role": user_message.role, "content": user_message.content},
        ):
            return

    def _stop(self) -> None:
        self._stop_callback(self.name)

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._is_any_queue_closed()

    def _is_any_queue_closed(self) -> bool:
        return (
            self._task_queue.is_closed()
            or self._agent_to_user_queue.is_closed()
        )
