"""Domain events (E1-E44) as defined in TD.md."""
from __future__ import annotations

from dataclasses import dataclass, field

from schemas.domain import DomainEvent
from schemas.ids import (
    CheckpointId,
    KnowledgeEntryId,
    PlanId,
    PlanStepId,
    StageId,
    TaskId,
)


@dataclass
class TaskReceived(DomainEvent):
    task_id: TaskId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskReceived"


@dataclass
class TaskPlanFinalized(DomainEvent):
    task_id: TaskId = field(default="")
    plan_id: PlanId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskPlanFinalized"


@dataclass
class TaskPlanRenewal(DomainEvent):
    task_id: TaskId = field(default="")
    plan_id: PlanId = field(default="")
    trigger: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskPlanRenewal"


@dataclass
class TaskPlanRevised(DomainEvent):
    task_id: TaskId = field(default="")
    plan_id: PlanId = field(default="")
    step_id: PlanStepId = field(default="")
    trigger: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskPlanRevised"


@dataclass
class TaskExecutionStarted(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    step_id: PlanStepId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskExecutionStarted"


@dataclass
class TaskSucceeded(DomainEvent):
    task_id: TaskId = field(default="")
    result: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskSucceeded"


@dataclass
class TaskFailed(DomainEvent):
    task_id: TaskId = field(default="")
    reason: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskFailed"


@dataclass
class TaskPaused(DomainEvent):
    task_id: TaskId = field(default="")
    reason: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskPaused"


@dataclass
class TaskResumed(DomainEvent):
    task_id: TaskId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskResumed"


@dataclass
class TaskCancelled(DomainEvent):
    task_id: TaskId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskCancelled"


@dataclass
class TaskTerminated(DomainEvent):
    task_id: TaskId = field(default="")
    reason: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskTerminated"


@dataclass
class TaskDelivered(DomainEvent):
    task_id: TaskId = field(default="")
    result: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskDelivered"


@dataclass
class TaskQualityCheckPassed(DomainEvent):
    task_id: TaskId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskQualityCheckPassed"


@dataclass
class TaskQualityCheckFailed(DomainEvent):
    task_id: TaskId = field(default="")
    feedback: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskQualityCheckFailed"


@dataclass
class TaskKnowledgeExtracted(DomainEvent):
    task_id: TaskId = field(default="")
    entry_id: KnowledgeEntryId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskKnowledgeExtracted"


@dataclass
class TaskKnowledgePersisted(DomainEvent):
    task_id: TaskId = field(default="")
    entry_id: KnowledgeEntryId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskKnowledgePersisted"


@dataclass
class CheckpointSaved(DomainEvent):
    task_id: TaskId = field(default="")
    checkpoint_id: CheckpointId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "CheckpointSaved"


@dataclass
class CheckpointRestored(DomainEvent):
    task_id: TaskId = field(default="")
    checkpoint_id: CheckpointId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "CheckpointRestored"


@dataclass
class PlanReviewPassed(DomainEvent):
    task_id: TaskId = field(default="")
    plan_id: PlanId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "PlanReviewPassed"


@dataclass
class PlanReviewFailed(DomainEvent):
    task_id: TaskId = field(default="")
    plan_id: PlanId = field(default="")
    feedback: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "PlanReviewFailed"


@dataclass
class StepResultProduced(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    step_id: PlanStepId = field(default="")
    result: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "StepResultProduced"


@dataclass
class StepResultEvaluationSucceeded(DomainEvent):
    task_id: TaskId = field(default="")
    step_id: PlanStepId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "StepResultEvaluationSucceeded"


@dataclass
class StepResultEvaluationFailed(DomainEvent):
    task_id: TaskId = field(default="")
    step_id: PlanStepId = field(default="")
    feedback: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "StepResultEvaluationFailed"


@dataclass
class TaskStepInterrupted(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    guidance: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "TaskStepInterrupted"


@dataclass
class ReusableKnowledgeLoaded(DomainEvent):
    task_id: TaskId = field(default="")
    step_id: PlanStepId = field(default="")
    count: int = field(default=0)
    def __post_init__(self) -> None:
        self.event_type = "ReusableKnowledgeLoaded"


@dataclass
class ModelSelected(DomainEvent):
    task_id: TaskId = field(default="")
    primary: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ModelSelected"


@dataclass
class ContextAssembled(DomainEvent):
    task_id: TaskId = field(default="")
    token_count: int = field(default=0)
    def __post_init__(self) -> None:
        self.event_type = "ContextAssembled"


@dataclass
class ContextTruncated(DomainEvent):
    task_id: TaskId = field(default="")
    original_tokens: int = field(default=0)
    trimmed_tokens: int = field(default=0)
    def __post_init__(self) -> None:
        self.event_type = "ContextTruncated"


@dataclass
class ReasoningStarted(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    iteration: int = field(default=0)
    def __post_init__(self) -> None:
        self.event_type = "ReasoningStarted"


@dataclass
class NextDecisionMade(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    decision_type: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "NextDecisionMade"


@dataclass
class ToolCallRequested(DomainEvent):
    task_id: TaskId = field(default="")
    tool_name: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ToolCallRequested"


@dataclass
class ToolCallDispatched(DomainEvent):
    task_id: TaskId = field(default="")
    tool_name: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ToolCallDispatched"


@dataclass
class ToolCallSucceeded(DomainEvent):
    task_id: TaskId = field(default="")
    tool_name: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ToolCallSucceeded"


@dataclass
class ToolCallFailed(DomainEvent):
    task_id: TaskId = field(default="")
    tool_name: str = field(default="")
    error_code: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ToolCallFailed"


@dataclass
class ResultInjected(DomainEvent):
    task_id: TaskId = field(default="")
    stage_id: StageId = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ResultInjected"


@dataclass
class ClarificationRequested(DomainEvent):
    task_id: TaskId = field(default="")
    question: str = field(default="")
    def __post_init__(self) -> None:
        self.event_type = "ClarificationRequested"
