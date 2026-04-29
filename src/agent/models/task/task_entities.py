from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import SnapshotId, TaskExecutionId, TaskId, TaskPlanId, TaskStepId
from agent.events.step_events import TaskStepCompleted, TaskStepInterrupted
from agent.events.task_events import (
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
    PENDING = "Pending"
    PLANNING = "Planning"
    EXECUTING = "Executing"
    QUALITY_CHECKING = "QualityChecking"
    SUCCEEDED = "Succeeded"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"
    TERMINATED = "Terminated"
    PAUSED = "Paused"


@dataclass
class Task(AggregateRoot):
    id: TaskId
    description: str
    status: TaskStatus = TaskStatus.PENDING
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
