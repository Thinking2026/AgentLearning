from __future__ import annotations

from dataclasses import dataclass, field

from schemas.domain import DomainEvent
from schemas.ids import TaskId, TaskPlanId, TaskExecutionId, SnapshotId


@dataclass
class TaskExecutionStarted(DomainEvent):
    """E8 — Task execution has started (or restarted from a step)."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    plan_id: TaskPlanId = field(default="")
    from_step_index: int = field(default=0)

    def __post_init__(self) -> None:
        self.event_type = "TaskExecutionStarted"
        self.aggregate_id = self.execution_id


@dataclass
class TaskPaused(DomainEvent):
    """E10 — Execution paused due to a recoverable (type-B) exception."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    reason: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskPaused"
        self.aggregate_id = self.execution_id


@dataclass
class UserResumeRequestProvided(DomainEvent):
    """E5 — User has requested to resume a paused execution."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    snapshot_id: SnapshotId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "UserResumeRequestProvided"
        self.aggregate_id = self.execution_id


@dataclass
class TaskResumed(DomainEvent):
    """E11 — Execution resumed from the latest snapshot."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    snapshot_id: SnapshotId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskResumed"
        self.aggregate_id = self.execution_id


@dataclass
class UserGuidanceSubmitted(DomainEvent):
    """E2 — User submitted corrective guidance during execution."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    guidance: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "UserGuidanceSubmitted"
        self.aggregate_id = self.execution_id


@dataclass
class TaskQualityCheckPassed(DomainEvent):
    """E18 — Quality check passed; task can be marked as succeeded."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskQualityCheckPassed"
        self.aggregate_id = self.execution_id


@dataclass
class TaskQualityCheckFailed(DomainEvent):
    """E19 — Quality check failed; full re-plan required."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    feedback: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskQualityCheckFailed"
        self.aggregate_id = self.execution_id


@dataclass
class TaskExecutionSnapshotSaved(DomainEvent):
    """E22 — Async snapshot of current execution state has been saved."""
    task_id: TaskId = field(default="")
    execution_id: TaskExecutionId = field(default="")
    snapshot_id: SnapshotId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskExecutionSnapshotSaved"
        self.aggregate_id = self.execution_id
