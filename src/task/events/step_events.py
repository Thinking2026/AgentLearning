from __future__ import annotations

from dataclasses import dataclass, field

from schemas.domain import DomainEvent
from schemas.ids import TaskExecutionId, TaskPlanId, TaskStepId


@dataclass
class TaskStepCompleted(DomainEvent):
    """E14 — Step completed successfully (step result evaluation passed)."""
    execution_id: TaskExecutionId = field(default="")
    step_id: TaskStepId = field(default="")
    step_index: int = field(default=0)
    result: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskStepCompleted"
        self.aggregate_id = self.step_id


@dataclass
class TaskStepSkipped(DomainEvent):
    """E15 — Step skipped because a prior step already achieved this step's goal."""
    execution_id: TaskExecutionId = field(default="")
    step_id: TaskStepId = field(default="")
    step_index: int = field(default=0)

    def __post_init__(self) -> None:
        self.event_type = "TaskStepSkipped"
        self.aggregate_id = self.step_id


@dataclass
class TaskStepInterrupted(DomainEvent):
    """E16 — Step interrupted by user guidance while in Running state."""
    execution_id: TaskExecutionId = field(default="")
    step_id: TaskStepId = field(default="")
    step_index: int = field(default=0)
    guidance: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskStepInterrupted"
        self.aggregate_id = self.step_id


@dataclass
class TaskStepFailed(DomainEvent):
    """E15 (failure variant) — Step failed (tool call limit exceeded, unrecoverable error, etc.)."""
    execution_id: TaskExecutionId = field(default="")
    step_id: TaskStepId = field(default="")
    step_index: int = field(default=0)
    reason: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskStepFailed"
        self.aggregate_id = self.step_id
