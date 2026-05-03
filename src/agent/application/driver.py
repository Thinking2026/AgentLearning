from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from schemas.ids import TaskId
from schemas.task import TaskResult
from schemas.types import UIMessage
from utils.log.log import Logger, zap

if TYPE_CHECKING:
    from agent.application.pipeline import Pipeline


# ---------------------------------------------------------------------------
# User ↔ Pipeline message protocol
# ---------------------------------------------------------------------------

class UserMsgType(str, Enum):
    """Canonical types for messages arriving from the user side."""
    NEW_TASK        = "NEW_TASK"         # Submit a new task
    CANCEL          = "CANCEL"           # Cancel the running task
    GUIDANCE        = "GUIDANCE"         # Mid-task steering / correction
    CLARIFICATION   = "CLARIFICATION"    # Reply to a clarification request
    RESUME          = "RESUME"           # Resume after a B-class pause
    CHECKPOINT_RUN  = "CHECKPOINT_RUN"   # Resume from latest checkpoint


@dataclass(frozen=True)
class UserCommand:
    """Normalised command produced by PipelineDriver from a raw UIMessage."""
    msg_type: UserMsgType
    task_id: TaskId | None
    content: str


# ---------------------------------------------------------------------------
# PipelineDriver
# ---------------------------------------------------------------------------

class PipelineDriver:
    """Protocol converter between UIMessages and Pipeline commands (TD §应用层).

    Responsibilities:
    1. Parse raw UIMessages into typed UserCommands (protocol definition).
    2. Dispatch each command to the appropriate Pipeline method.
    3. Wire the send_to_user callback so Pipeline can push progress back.

    Interaction flow:
      UserThread.submit(UIMessage)
        → PipelineThread → PipelineDriver.route_user_message()
          → Pipeline.run() / cancel() / provide_guidance() / …
      Pipeline._notify_user(UIMessage)
        → send_to_user callback → AgentToUserQueue → UserThread
    """

    # Metadata key used by callers to declare message intent explicitly.
    # If absent, the driver infers intent from context (see _classify).
    MSG_TYPE_KEY = "msg_type"

    def __init__(
        self,
        pipeline: Pipeline,
        send_to_user: Callable[[UIMessage], None],
        logger: Logger,
    ) -> None:
        self._pipeline = pipeline
        self._logger = logger
        self._pipeline.set_send_to_user(send_to_user)

    # ------------------------------------------------------------------
    # Task lifecycle entry points
    # ------------------------------------------------------------------

    def submit_task(self, task_id: TaskId, task_description: str) -> TaskResult:
        """Run a task synchronously and return the result."""
        return self._pipeline.run(task_id=task_id, task_description=task_description)

    def submit_task_from_checkpoint(
        self, task_id: TaskId, task_description: str
    ) -> TaskResult:
        """Restore from the latest checkpoint and resume execution."""
        return self._pipeline.run_from_checkpoint(
            task_id=task_id, task_description=task_description
        )

    # ------------------------------------------------------------------
    # In-flight user message routing
    # ------------------------------------------------------------------

    def route_user_message(self, message: UIMessage) -> None:
        """Convert a UIMessage to a Pipeline command and dispatch it.

        Protocol (checked in order):
          metadata["msg_type"] == "CANCEL"        → Pipeline.cancel()
          metadata["msg_type"] == "RESUME"        → Pipeline.resume()
          metadata["msg_type"] == "CLARIFICATION" → Pipeline.provide_clarification()
          metadata["msg_type"] == "GUIDANCE"      → Pipeline.provide_guidance()
          (no msg_type key)                        → inferred as GUIDANCE
        """
        cmd = self._parse(message)
        self._dispatch(cmd)

    # ------------------------------------------------------------------
    # Protocol parsing
    # ------------------------------------------------------------------

    def _parse(self, message: UIMessage) -> UserCommand:
        """Normalise a UIMessage into a typed UserCommand."""
        raw_type = message.metadata.get(self.MSG_TYPE_KEY, "")
        try:
            msg_type = UserMsgType(raw_type.upper()) if raw_type else self._infer_type(message)
        except ValueError:
            msg_type = self._infer_type(message)

        task_id_str = message.metadata.get("task_id")
        task_id = TaskId(task_id_str) if task_id_str else None

        return UserCommand(msg_type=msg_type, task_id=task_id, content=message.content.strip())

    @staticmethod
    def _infer_type(message: UIMessage) -> UserMsgType:
        """Infer message type from content when no explicit type is set."""
        content = message.content.strip().lower()
        if content in {"cancel", "stop", "abort"}:
            return UserMsgType.CANCEL
        if content in {"resume", "continue", "go"}:
            return UserMsgType.RESUME
        return UserMsgType.GUIDANCE

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, cmd: UserCommand) -> None:
        self._logger.info(
            "PipelineDriver dispatch",
            zap.any("type", cmd.msg_type),
            zap.any("content", cmd.content[:80] if cmd.content else ""),
        )
        if cmd.msg_type == UserMsgType.CANCEL:
            self._pipeline.cancel()
        elif cmd.msg_type == UserMsgType.RESUME:
            self._pipeline.resume()
        elif cmd.msg_type == UserMsgType.CLARIFICATION:
            self._pipeline.provide_clarification(cmd.content)
        elif cmd.msg_type in (UserMsgType.GUIDANCE, UserMsgType.NEW_TASK):
            self._pipeline.provide_guidance(cmd.content)
        else:
            self._logger.info("PipelineDriver: unhandled msg_type", zap.any("type", cmd.msg_type))
