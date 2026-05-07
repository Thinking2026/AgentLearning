from __future__ import annotations

from typing import TYPE_CHECKING

from agent.events.events import DomainEvent, TaskCancelled, TaskPaused, UserClarificationRequested, UserCommand, ALL_EVENTS
from schemas.ids import TaskId, CheckpointId
from schemas.types import UserCommandType, UserMessage, UserMsgType
from schemas.task import TaskResult
from schemas.event_bus import EventBus

if TYPE_CHECKING:
    from agent.application.pipeline import Pipeline
    from agent.application.pipeline_thread import PipelineThread

class PipelineDriver:
    # Metadata key used by callers to declare message intent explicitly.
    # If absent, the driver infers intent from context (see _classify).
    MSG_TYPE_KEY = "msg_type"

    def __init__(
        self,
        loop_user_messages_timeout_seconds: float,
        event_bus: EventBus,
        thread: PipelineThread,
    ) -> None:
        self._loop_user_messages_timeout_seconds = loop_user_messages_timeout_seconds if loop_user_messages_timeout_seconds > 0 else 0.5
        self._thread = thread
        for event_type in ALL_EVENTS:
            event_bus.subscribe(event_type, self.publish_event)

    def use_pipeline(self, pipeline: Pipeline) -> None:
        pipeline.set_driver(self)

    # ------------------------------------------------------------------
    # Task lifecycle entry points
    # ------------------------------------------------------------------

    def submit_task(self, task_description: str, pipeline: Pipeline) -> TaskResult:
        """Run a task synchronously and return the result."""
        return pipeline.run(task_description=task_description)

    def submit_task_from_checkpoint(
        self, task_id: TaskId, checkpoint_id: CheckpointId, pipeline: Pipeline)-> TaskResult:
        """Restore from the latest checkpoint and resume execution."""
        return pipeline.continue_from_checkpoint(
            task_id=task_id, cpt_id=checkpoint_id
        )

    def loop_user_messages(self, timeout: float) -> UserCommand | None:
        UserMessage = self._thread.loop_user_message(timeout)
        if UserMessage is not None:
            return self.convert_user_message(UserMessage)
        return None

    def convert_user_message(self, message: UserMessage) -> UserCommand | None:
        if message is not None:
            if message.msg_type == UserMsgType.CANCEL:
                return UserCommand(type=UserCommandType.CANCEL, task_id=message.task_id, user_id=message.user_id) 
            elif message.msg_type == UserMsgType.RESUME:
                return UserCommand(type=UserCommandType.RESUME, task_id=message.task_id, user_id=message.user_id)
            elif message.msg_type == UserMsgType.CLARIFICATION:                 
                return UserCommand(type=UserCommandType.CLARIFICATION, task_id=message.task_id, user_id=message.user_id, content=message.content)
            elif message.msg_type == UserMsgType.GUIDANCE:
                return UserCommand(type=UserCommandType.GUIDANCE, task_id=message.task_id, user_id=message.user_id, content=message.content)
        return None

    def convert_pipeline_event(self, event: DomainEvent) -> UserMessage | None:
        if isinstance(event, TaskCancelled):
            return UserMessage(type=UserMsgType.CANCEL, task_id=event.task_id, user_id=event.user_id, content=event.reason)
        elif isinstance(event, TaskPaused):
            return UserMessage(type=UserMsgType.PAUSE_FROM_AGENT, task_id=event.task_id, user_id=event.user_id, content=event.reason)
        elif isinstance(event, UserClarificationRequested):
            return UserMessage(type=UserMsgType.CLARIFICATION, task_id=event.task_id, user_id=event.user_id, content=event.question)

        return UserMessage(type=UserMsgType.PROGRESS_FROM_AGENT, task_id=event.task_id, user_id=event.user_id, content=event.content)
    
    def publish_event(self, event: DomainEvent) -> None:
        msg = self.convert_pipeline_event(event)
        if msg is not None:
            self._thread.publish_msg_to_user(msg)