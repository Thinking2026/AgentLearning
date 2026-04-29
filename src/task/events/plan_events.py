from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from schemas.domain import DomainEvent
from schemas.ids import TaskId, TaskPlanId


class PlanUpdateTrigger(str, Enum):
    QUALITY_CHECK_FAILED = "QualityCheckFailed"
    STEP_EVAL_FAILED = "StepEvalFailed"
    USER_CORRECTION = "UserCorrection"
    PLAN_EVAL_FAILED = "PlanEvalFailed"


class PlanUpdateScope(str, Enum):
    PARTIAL = "partial"
    FULL = "full"


@dataclass
class TaskPlanFinalized(DomainEvent):
    """E6 — Execution plan has been created (first version only)."""
    task_id: TaskId = field(default="")
    plan_id: TaskPlanId = field(default="")
    version: int = field(default=1)

    def __post_init__(self) -> None:
        self.event_type = "TaskPlanFinalized"
        self.aggregate_id = self.plan_id


@dataclass
class TaskPlanUpdated(DomainEvent):
    """E7 — Execution plan has been updated (version >= 2)."""
    task_id: TaskId = field(default="")
    plan_id: TaskPlanId = field(default="")
    version: int = field(default=2)
    trigger: PlanUpdateTrigger = field(default=PlanUpdateTrigger.USER_CORRECTION)
    scope: PlanUpdateScope = field(default=PlanUpdateScope.FULL)
    from_step_index: int | None = field(default=None)

    def __post_init__(self) -> None:
        self.event_type = "TaskPlanUpdated"
        self.aggregate_id = self.plan_id


@dataclass
class TaskPlanReviewPassed(DomainEvent):
    """E23 — Plan review passed; execution may begin."""
    task_id: TaskId = field(default="")
    plan_id: TaskPlanId = field(default="")
    version: int = field(default=1)

    def __post_init__(self) -> None:
        self.event_type = "TaskPlanReviewPassed"
        self.aggregate_id = self.plan_id


@dataclass
class TaskPlanReviewFailed(DomainEvent):
    """E24 — Plan review failed; UpdatePlan (scope: full) must follow."""
    task_id: TaskId = field(default="")
    plan_id: TaskPlanId = field(default="")
    version: int = field(default=1)
    review_feedback: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "TaskPlanReviewFailed"
        self.aggregate_id = self.plan_id
