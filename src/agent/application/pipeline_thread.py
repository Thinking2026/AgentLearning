from __future__ import annotations

import threading
from typing import Callable
from uuid import uuid4

from config import ConfigReader
from agent.factory.agent_factory import AgentFactory
from agent.application.driver import PipelineDriver
from schemas.ids import TaskId
from schemas.task import TaskResult
from schemas.types import UserMessage
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
        self._factory: AgentFactory | None = None
        self._active_driver: PipelineDriver | None = None
        try:
            self._logger = Logger.get_instance()
            self._factory = AgentFactory.from_config(config)
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

                task_id = TaskId(f"task_{uuid4().hex}")
                task_description = user_message.content.strip()

                pipeline = self._factory.build_pipeline(self._active_driver)
                self._active_driver.set_pipeline(pipeline)
                # Build a fresh Pipeline + Driver for each task
                result = self._active_driver.submit_task(task_id, task_description)
                if not result.succeeded:
                    self._logger.error(
                        "Task execution failed",
                        zap.any("task_id", task_id),
                        zap.any("error", result.error),
                    )
                self._send_task_completed(result)

        except Exception as exc:
            self._logger.error("PipelineThread crashed", zap.any("error", exc))
        finally:
            self._stop()

    def _send_task_completed(self, result: TaskResult) -> None:
        msg = UserMessage(
            content=result.output,
            metadata={"succeeded": result.succeeded, "error": result.error_reason},
        )
        self.publish_event(msg)

    def _stop(self) -> None:
        self._stop_callback(self.name)

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._is_any_queue_closed()

    def _is_any_queue_closed(self) -> bool:
        return (
            self._task_queue.is_closed()
            or self._agent_msg_queue.is_closed()
            or self._user_msg_queue.is_closed()
        )
