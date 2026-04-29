from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import SnapshotId, TaskExecutionId, TaskId, TaskPlanId, TaskStepId
from task.events.execution_events import (
    TaskExecutionSnapshotSaved,
    TaskExecutionStarted,
    TaskPaused,
    TaskQualityCheckFailed,
    TaskQualityCheckPassed,
    TaskResumed,
    UserGuidanceSubmitted,
    UserResumeRequestProvided,
)
from task.events.plan_events import (
    PlanUpdateScope,
    PlanUpdateTrigger,
    TaskPlanFinalized,
    TaskPlanReviewFailed,
    TaskPlanReviewPassed,
    TaskPlanUpdated,
)
from task.events.step_events import TaskStepCompleted, TaskStepInterrupted, TaskStepSkipped
from task.events.task_events import (
    TaskCancelled,
    TaskKnowledgePersisted,
    TaskReceived,
    TaskResultDelivered,
    TaskSucceeded,
    TaskTerminated,
)


class DomainRuleViolation(ValueError):
    """Raised when a bounded-context invariant would be violated."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _event(event_type: type[DomainEvent], **kwargs: Any) -> DomainEvent:
    return event_type(event_type="", aggregate_id="", **kwargs)


class TaskStatus(str, Enum):
    INIT = "Init"
    PLANNING = "Planning"
    EXECUTING = "Executing"
    QUALITY_CHECKING = "QualityChecking"
    SUCCEEDED = "Succeeded"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"
    TERMINATED = "Terminated"


@dataclass
class Task(AggregateRoot):
    """Task Management aggregate root for the user-visible task lifecycle."""

    id: TaskId
    description: str
    status: TaskStatus = TaskStatus.INIT
    plan_id: TaskPlanId | None = None
    execution_id: TaskExecutionId | None = None
    result: str | None = None
    knowledge: str | None = None
    knowledge_persisted: bool = False

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def submit(cls, task_description: str, task_id: TaskId | None = None) -> Task:
        if not task_description.strip():
            raise DomainRuleViolation("task description must not be empty")
        task = cls(id=task_id or TaskId(_new_id("task")), description=task_description)
        task.status = TaskStatus.PLANNING
        task._record(_event(TaskReceived, task_id=task.id, goal=task_description))
        return task

    def attach_plan(self, plan_id: TaskPlanId) -> None:
        self._ensure_active()
        if self.status != TaskStatus.PLANNING:
            raise DomainRuleViolation("task plan can only be attached while planning")
        self.plan_id = plan_id

    def start_execution(self, execution_id: TaskExecutionId) -> None:
        self._ensure_active()
        if self.plan_id is None:
            raise DomainRuleViolation("task cannot execute before a plan is finalized")
        if self.status not in {TaskStatus.PLANNING, TaskStatus.QUALITY_CHECKING}:
            raise DomainRuleViolation("task execution can only start after planning or replanning")
        self.execution_id = execution_id
        self.status = TaskStatus.EXECUTING

    def begin_quality_check(self) -> None:
        self._ensure_active()
        if self.status != TaskStatus.EXECUTING:
            raise DomainRuleViolation("quality check can only start after execution")
        self.status = TaskStatus.QUALITY_CHECKING

    def complete(self) -> None:
        self._ensure_active()
        if self.status != TaskStatus.QUALITY_CHECKING:
            raise DomainRuleViolation("task can only succeed after quality checking")
        self.status = TaskStatus.SUCCEEDED
        self._record(_event(TaskSucceeded, task_id=self.id))

    def cancel(self) -> None:
        if self.status in {TaskStatus.CANCELLED, TaskStatus.TERMINATED, TaskStatus.DELIVERED}:
            raise DomainRuleViolation("terminal task cannot be cancelled")
        self.status = TaskStatus.CANCELLED
        self._record(_event(TaskCancelled, task_id=self.id))

    def terminate(self, reason: str) -> None:
        if self.status in {TaskStatus.CANCELLED, TaskStatus.TERMINATED, TaskStatus.DELIVERED}:
            raise DomainRuleViolation("terminal task cannot be terminated")
        if not reason.strip():
            raise DomainRuleViolation("termination reason must not be empty")
        self.status = TaskStatus.TERMINATED
        self._record(_event(TaskTerminated, task_id=self.id, reason=reason))

    def deliver_result(self, result: str) -> None:
        self._ensure_active(allow_succeeded=True)
        if self.status != TaskStatus.SUCCEEDED:
            raise DomainRuleViolation("result can only be delivered after task succeeded")
        self.result = result
        self.status = TaskStatus.DELIVERED
        self._record(_event(TaskResultDelivered, task_id=self.id, result=result))

    def persist_knowledge(self, knowledge: str) -> None:
        if self.status != TaskStatus.DELIVERED:
            raise DomainRuleViolation("knowledge can only be persisted after result delivery")
        if not knowledge.strip():
            raise DomainRuleViolation("knowledge must not be empty")
        self.knowledge = knowledge
        self.knowledge_persisted = True
        self._record(_event(TaskKnowledgePersisted, task_id=self.id))

    def _ensure_active(self, *, allow_succeeded: bool = False) -> None:
        terminal = {TaskStatus.CANCELLED, TaskStatus.TERMINATED, TaskStatus.DELIVERED}
        if not allow_succeeded:
            terminal.add(TaskStatus.SUCCEEDED)
        if self.status in terminal:
            raise DomainRuleViolation(f"task in {self.status.value} cannot accept this command")


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


class TaskExecutionStatus(str, Enum):
    IDLE = "Idle"
    RUNNING = "Running"
    PAUSED = "Paused"
    QUALITY_CHECKING = "QualityChecking"
    DONE = "Done"
    CANCELLED = "Cancelled"
    TERMINATED = "Terminated"


class ManagedStepStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    SKIPPED = "Skipped"
    INTERRUPTED = "Interrupted"


@dataclass
class TaskExecution(AggregateRoot):
    """Coordinates task-level step progress, snapshots, pause and quality checks."""

    id: TaskExecutionId
    task_id: TaskId
    plan_id: TaskPlanId
    step_ids: tuple[TaskStepId, ...]
    status: TaskExecutionStatus = TaskExecutionStatus.IDLE
    from_step_index: int = 0
    current_step_index: int | None = None
    step_statuses: dict[TaskStepId, ManagedStepStatus] = field(default_factory=dict)
    snapshots: list[SnapshotId] = field(default_factory=list)

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)
        if not self.step_statuses:
            self.step_statuses = {step_id: ManagedStepStatus.PENDING for step_id in self.step_ids}

    @classmethod
    def start(
        cls,
        task_id: TaskId,
        plan_id: TaskPlanId,
        step_ids: list[TaskStepId] | tuple[TaskStepId, ...],
        from_step: int = 0,
        execution_id: TaskExecutionId | None = None,
    ) -> TaskExecution:
        if from_step < 0:
            raise DomainRuleViolation("from_step must be non-negative")
        execution = cls(
            id=execution_id or TaskExecutionId(_new_id("exec")),
            task_id=task_id,
            plan_id=plan_id,
            step_ids=tuple(step_ids),
            status=TaskExecutionStatus.RUNNING,
            from_step_index=from_step,
        )
        execution._record(
            _event(
                TaskExecutionStarted,
                task_id=task_id,
                execution_id=execution.id,
                plan_id=plan_id,
                from_step_index=from_step,
            )
        )
        return execution

    def execute_step(self, step_index: int, *, prior_goal_already_achieved: bool = False) -> None:
        self._ensure_running()
        if step_index >= len(self.step_ids) or step_index < 0:
            raise DomainRuleViolation("step index is out of range")
        if self.current_step_index is not None:
            raise DomainRuleViolation("only one step can be running at a time")
        step_id = self.step_ids[step_index]
        if self.step_statuses[step_id] != ManagedStepStatus.PENDING:
            raise DomainRuleViolation("only pending steps can be executed")
        if prior_goal_already_achieved:
            self.step_statuses[step_id] = ManagedStepStatus.SKIPPED
            self._record(
                _event(
                    TaskStepSkipped,
                    execution_id=self.id,
                    step_id=step_id,
                    step_index=step_index,
                )
            )
            return
        self.step_statuses[step_id] = ManagedStepStatus.RUNNING
        self.current_step_index = step_index

    def mark_step_completed(self, step_index: int, result: str) -> None:
        step_id = self._require_current_step(step_index)
        self.step_statuses[step_id] = ManagedStepStatus.COMPLETED
        self.current_step_index = None
        self._record(
            _event(
                TaskStepCompleted,
                execution_id=self.id,
                step_id=step_id,
                step_index=step_index,
                result=result,
            )
        )

    def submit_guidance(self, guidance: str) -> None:
        self._ensure_running()
        if self.current_step_index is None:
            raise DomainRuleViolation("guidance can only interrupt a running step")
        if not guidance.strip():
            raise DomainRuleViolation("guidance must not be empty")
        step_id = self.step_ids[self.current_step_index]
        self.step_statuses[step_id] = ManagedStepStatus.INTERRUPTED
        self._record(_event(UserGuidanceSubmitted, task_id=self.task_id, execution_id=self.id, guidance=guidance))
        self._record(
            _event(
                TaskStepInterrupted,
                execution_id=self.id,
                step_id=step_id,
                step_index=self.current_step_index,
                guidance=guidance,
            )
        )
        self.current_step_index = None

    def pause(self, reason: str) -> None:
        self._ensure_running()
        if not reason.strip():
            raise DomainRuleViolation("pause reason must not be empty")
        self.status = TaskExecutionStatus.PAUSED
        self._record(_event(TaskPaused, task_id=self.task_id, execution_id=self.id, reason=reason))

    def resume(self, snapshot_id: SnapshotId | None = None) -> None:
        if self.status != TaskExecutionStatus.PAUSED:
            raise DomainRuleViolation("only paused execution can resume")
        selected_snapshot = snapshot_id or (self.snapshots[-1] if self.snapshots else None)
        if selected_snapshot is None or selected_snapshot not in self.snapshots:
            raise DomainRuleViolation("resume requires an existing execution snapshot")
        self._record(
            _event(
                UserResumeRequestProvided,
                task_id=self.task_id,
                execution_id=self.id,
                snapshot_id=selected_snapshot,
            )
        )
        self.status = TaskExecutionStatus.RUNNING
        self._record(_event(TaskResumed, task_id=self.task_id, execution_id=self.id, snapshot_id=selected_snapshot))

    def save_snapshot(self, snapshot_id: SnapshotId | None = None) -> SnapshotId:
        if self.status != TaskExecutionStatus.RUNNING:
            raise DomainRuleViolation("snapshot can only be saved while running")
        snapshot = snapshot_id or SnapshotId(_new_id("snapshot"))
        self.snapshots.append(snapshot)
        self._record(_event(TaskExecutionSnapshotSaved, task_id=self.task_id, execution_id=self.id, snapshot_id=snapshot))
        return snapshot

    def check_quality(self, passed: bool, feedback: str = "") -> None:
        if not self._all_steps_finished():
            raise DomainRuleViolation("quality check requires all steps completed or skipped")
        self.status = TaskExecutionStatus.QUALITY_CHECKING
        if passed:
            self.status = TaskExecutionStatus.DONE
            self._record(_event(TaskQualityCheckPassed, task_id=self.task_id, execution_id=self.id))
            return
        self._record(_event(TaskQualityCheckFailed, task_id=self.task_id, execution_id=self.id, feedback=feedback))

    def _ensure_running(self) -> None:
        if self.status != TaskExecutionStatus.RUNNING:
            raise DomainRuleViolation("execution command requires Running state")

    def _require_current_step(self, step_index: int) -> TaskStepId:
        self._ensure_running()
        if self.current_step_index != step_index:
            raise DomainRuleViolation("step is not the current running step")
        return self.step_ids[step_index]

    def _all_steps_finished(self) -> bool:
        return all(
            status in {ManagedStepStatus.COMPLETED, ManagedStepStatus.SKIPPED}
            for status in self.step_statuses.values()
        )


class TaskStepStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    SKIPPED = "Skipped"
    INTERRUPTED = "Interrupted"


@dataclass
class TaskStep(AggregateRoot):
    """Task-layer step aggregate; hides the internal Agent execution loop."""

    id: TaskStepId
    execution_id: TaskExecutionId
    plan_id: TaskPlanId
    step_index: int
    goal: str
    input_context: str = ""
    output: str | None = None
    status: TaskStepStatus = TaskStepStatus.PENDING

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def start(
        cls,
        execution_id: TaskExecutionId,
        plan_id: TaskPlanId,
        step_index: int,
        goal: str,
        input_context: str = "",
        step_id: TaskStepId | None = None,
    ) -> TaskStep:
        if not goal.strip():
            raise DomainRuleViolation("step goal must not be empty")
        return cls(
            id=step_id or TaskStepId(_new_id("step")),
            execution_id=execution_id,
            plan_id=plan_id,
            step_index=step_index,
            goal=goal,
            input_context=input_context,
            status=TaskStepStatus.RUNNING,
        )

    @classmethod
    def execute(
        cls,
        execution_id: TaskExecutionId,
        plan_id: TaskPlanId,
        step_index: int,
        input_context: str,
        result: str,
        goal: str = "",
        step_id: TaskStepId | None = None,
    ) -> TaskStep:
        step = cls.start(execution_id, plan_id, step_index, goal or input_context, input_context, step_id)
        step.complete(result)
        return step

    def complete(self, result: str) -> None:
        if self.status != TaskStepStatus.RUNNING:
            raise DomainRuleViolation("only running step can complete")
        self.status = TaskStepStatus.COMPLETED
        self.output = result
        self._record(
            _event(
                TaskStepCompleted,
                execution_id=self.execution_id,
                step_id=self.id,
                step_index=self.step_index,
                result=result,
            )
        )

    def interrupt(self, guidance: str) -> None:
        if self.status != TaskStepStatus.RUNNING:
            raise DomainRuleViolation("only running step can be interrupted")
        if not guidance.strip():
            raise DomainRuleViolation("guidance must not be empty")
        self.status = TaskStepStatus.INTERRUPTED
        self.input_context = ""
        self.output = None
        self._record(
            _event(
                TaskStepInterrupted,
                execution_id=self.execution_id,
                step_id=self.id,
                step_index=self.step_index,
                guidance=guidance,
            )
        )


def _normalize_steps(
    steps: list[PlanStep] | tuple[PlanStep, ...],
    *,
    start_order: int = 0,
) -> tuple[PlanStep, ...]:
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
