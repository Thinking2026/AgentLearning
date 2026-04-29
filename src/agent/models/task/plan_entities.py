from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.domain import AggregateRoot
from schemas.ids import TaskId, TaskPlanId, TaskStepId
from agent.events.plan_events import (
    PlanUpdateScope,
    PlanUpdateTrigger,
    TaskPlanFinalized,
    TaskPlanReviewFailed,
    TaskPlanReviewPassed,
    TaskPlanUpdated,
)
from agent.models.task.task_entities import DomainRuleViolation, _event, _new_id


@dataclass(frozen=True)
class PlanStep:
    id: TaskStepId
    goal: str
    order: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanVersion:
    version: int
    steps: tuple[PlanStep, ...]
    trigger: PlanUpdateTrigger | None = None
    scope: PlanUpdateScope | None = None
    from_step_index: int | None = None
    review_feedback: str | None = None


@dataclass
class TaskPlan(AggregateRoot):
    """Versioned execution plan aggregate."""

    id: TaskPlanId
    task_id: TaskId
    versions: tuple[PlanVersion, ...]
    review_passed: bool = False
    requires_full_update: bool = False
    checkpoint_step_index: int | None = None

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @property
    def version(self) -> int:
        return self.current_version.version

    @property
    def current_version(self) -> PlanVersion:
        return self.versions[-1]

    @property
    def steps(self) -> tuple[PlanStep, ...]:
        return self.current_version.steps

    @classmethod
    def create(
        cls,
        task_id: TaskId,
        steps: list[PlanStep] | tuple[PlanStep, ...],
        plan_id: TaskPlanId | None = None,
    ) -> TaskPlan:
        normalized = _normalize_steps(steps)
        plan = cls(
            id=plan_id or TaskPlanId(_new_id("plan")),
            task_id=task_id,
            versions=(PlanVersion(version=1, steps=normalized),),
        )
        plan._record(_event(TaskPlanFinalized, task_id=task_id, plan_id=plan.id, version=1))
        return plan

    def update(
        self,
        reason: PlanUpdateTrigger,
        scope: PlanUpdateScope,
        steps: list[PlanStep] | tuple[PlanStep, ...],
        from_cursor: int | None = None,
        review_feedback: str | None = None,
    ) -> None:
        if self.requires_full_update and scope != PlanUpdateScope.FULL:
            raise DomainRuleViolation("plan review failure requires a full update")
        if scope == PlanUpdateScope.PARTIAL:
            if from_cursor is None:
                raise DomainRuleViolation("partial plan update requires from_cursor")
            if self.checkpoint_step_index is not None and from_cursor <= self.checkpoint_step_index:
                raise DomainRuleViolation("partial update can only modify steps after checkpoint")
            prefix = self.steps[:from_cursor]
            new_steps = prefix + _normalize_steps(steps, start_order=from_cursor)
        else:
            new_steps = _normalize_steps(steps)

        new_version = PlanVersion(
            version=self.version + 1,
            steps=new_steps,
            trigger=reason,
            scope=scope,
            from_step_index=from_cursor,
            review_feedback=review_feedback,
        )
        self.versions = self.versions + (new_version,)
        self.review_passed = False
        self.requires_full_update = False
        self._record(
            _event(
                TaskPlanUpdated,
                task_id=self.task_id,
                plan_id=self.id,
                version=new_version.version,
                trigger=reason,
                scope=scope,
                from_step_index=from_cursor,
            )
        )
    
    def check_review_result(self) -> bool:
        return self.review_passed

    def review(self, passed: bool, feedback: str = "") -> None:
        if not self.versions:
            raise DomainRuleViolation("plan must be finalized before review")
        if passed:
            self.review_passed = True
            self.requires_full_update = False
            self._record(
                _event(
                    TaskPlanReviewPassed,
                    task_id=self.task_id,
                    plan_id=self.id,
                    version=self.version,
                )
            )
            return
        self.review_passed = False
        self.requires_full_update = True
        self._record(
            _event(
                TaskPlanReviewFailed,
                task_id=self.task_id,
                plan_id=self.id,
                version=self.version,
                review_feedback=feedback,
            )
        )

    def record_checkpoint_cursor(self, step_index: int) -> None:
        self.checkpoint_step_index = step_index

def _normalize_steps(steps: list[PlanStep] | tuple[PlanStep, ...], *, start_order: int = 0,) -> tuple[PlanStep, ...]:
    if not steps:
        raise DomainRuleViolation("plan must contain at least one step")
    normalized: list[PlanStep] = []
    for offset, step in enumerate(steps):
        if not step.goal.strip():
            raise DomainRuleViolation("plan step goal must not be empty")
        normalized.append(
            PlanStep(
                id=step.id or TaskStepId(_new_id("step")),
                goal=step.goal,
                order=start_order + offset,
                metadata=dict(step.metadata),
            )
        )
    return tuple(normalized)