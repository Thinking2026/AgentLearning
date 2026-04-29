from __future__ import annotations

from dataclasses import dataclass, field

from schemas.domain import DomainEvent
from schemas.ids import TaskId, TaskPlanId, TaskExecutionId


@dataclass
class TaskReceived(DomainEvent):
    """E1 — Task has been accepted from the user."""
    task_id: TaskId = field(default="")
    goal: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskReceived"
        self.aggregate_id = self.task_id


@dataclass
class TaskSucceeded(DomainEvent):
    """E9 — Task completed successfully (quality check passed)."""
    task_id: TaskId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskSucceeded"
        self.aggregate_id = self.task_id


@dataclass
class TaskCancelled(DomainEvent):
    """E12 — Task cancelled by the user. Terminal, not resumable."""
    task_id: TaskId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskCancelled"
        self.aggregate_id = self.task_id


@dataclass
class TaskTerminated(DomainEvent):
    """E13 — Task terminated by the system (unrecoverable failure). Terminal, not resumable."""
    task_id: TaskId = field(default="")
    reason: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskTerminated"
        self.aggregate_id = self.task_id


@dataclass
class TaskResultDelivered(DomainEvent):
    """E20 — Task result delivered to the user."""
    task_id: TaskId = field(default="")
    result: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskResultDelivered"
        self.aggregate_id = self.task_id


@dataclass
class TaskKnowledgePersisted(DomainEvent):
    """E21 — Reusable knowledge extracted from this task has been persisted."""
    task_id: TaskId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskKnowledgePersisted"
        self.aggregate_id = self.task_id
