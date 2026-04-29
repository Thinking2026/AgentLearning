from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from schemas.consts import SessionStatus
from schemas.domain import AggregateRoot
from schemas.ids import SnapshotId, TaskExecutionId, TaskId, TaskPlanId, TaskStepId
from agent.events.execution_events import (
    TaskExecutionSnapshotSaved,
    TaskExecutionStarted,
    TaskPaused,
    TaskQualityCheckFailed,
    TaskQualityCheckPassed,
    TaskResumed,
    UserGuidanceSubmitted,
    UserResumeRequestProvided,
)
from agent.events.step_events import TaskStepCompleted, TaskStepInterrupted, TaskStepSkipped
from agent.models.task.task_entities import DomainRuleViolation, _event, _new_id


class Session:
    def __init__(self, status: SessionStatus = SessionStatus.NEW_TASK) -> None:
        self._status = status
        self._lock = threading.Lock()

    def get_status(self) -> SessionStatus:
        with self._lock:
            return self._status

    def set_status(self, status: SessionStatus) -> None:
        with self._lock:
            self._status = status

    def begin(self) -> None:
        self.set_status(SessionStatus.IN_PROGRESS)

    def reset(self) -> None:
        self.set_status(SessionStatus.NEW_TASK)


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
