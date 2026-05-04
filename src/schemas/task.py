from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from schemas.domain import DomainEvent
from schemas.ids import (
    CheckpointId,
    KnowledgeEntryId,
    PlanId,
    PlanStepId,
    TaskId,
    UserId,
)
from schemas.types import KnowledgeEntry, LLMMessage, LLMResponse, ToolCall, UserPreferenceEntry


# ---------------------------------------------------------------------------
# StageStatus
# ---------------------------------------------------------------------------

class StageStatus(str, Enum):
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    INTERRUPTED  = "INTERRUPTED"
    PAUSED       = "PAUSED"
    FAILED       = "FAILED"


# ---------------------------------------------------------------------------
# CheckpointEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckpointEntry:
    id: CheckpointId
    task_id: TaskId
    plan_id: PlanId
    stage_order: int
    conversation_checkpoint: list[LLMMessage]
    created_at: datetime


# ---------------------------------------------------------------------------
# EvaluationRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvaluationRecord:
    target_type: str   # "task" | "step" | "plan"
    target_id: str
    passed: bool
    feedback: str
    evaluated_at: datetime
    need_user_clarification: bool = field(default=False)
    clarification_question: str = field(default="")


# ---------------------------------------------------------------------------
# KnowledgeEntryStatus / KnowledgeExtracted / KnowledgeIndexed
# ---------------------------------------------------------------------------

class KnowledgeEntryStatus(str, Enum):
    EXTRACTED = "Extracted"
    INDEXED = "Indexed"


@dataclass
class KnowledgeExtracted(DomainEvent):
    knowledge_entry_id: KnowledgeEntryId = field(default="")
    task_id: TaskId = field(default="")
    content: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "KnowledgeExtracted"
        self.aggregate_id = self.knowledge_entry_id


@dataclass
class KnowledgeIndexed(DomainEvent):
    knowledge_entry_id: KnowledgeEntryId = field(default="")
    task_id: TaskId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "KnowledgeIndexed"
        self.aggregate_id = self.knowledge_entry_id


# ---------------------------------------------------------------------------
# ProviderCapabilities / RoutingDecision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    cognitive_complexity: list[str]
    best_scenarios: list[str]
    top_strengths: list[str]
    cost_tier: str
    latency_tier: str
    context_size: int


@dataclass(frozen=True)
class RoutingDecision:
    """Provider names only; Pipeline resolves to LLMGateway instances via registry."""
    primary: str
    fallbacks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PlanStep / TaskAnalysis / PlanUpdateTrigger
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelatedPreferenceEntry:
    entry: UserPreferenceEntry
    confidence: float  # 0-1


@dataclass(frozen=True)
class RelatedKnowledgeEntry:
    entry: KnowledgeEntry
    confidence: float  # 0-1


@dataclass(frozen=True)
class PlanStep:
    id: PlanStepId
    goal: str
    description: str
    order: int
    key_results: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskFeature:
    task_type: str
    complexity: str
    required_tools: list[str]
    estimated_steps: int
    notes: str
    preferred_scenarios: list[str] = field(default_factory=list)
    required_strengths: list[str] = field(default_factory=list)
    min_context_size: int = 0
    prefer_low_cost: bool = False
    prefer_low_latency: bool = False


class PlanUpdateTrigger(str, Enum):
    QUALITY_CHECK_FAILED = "QUALITY_CHECK_FAILED"
    PLAN_REVIEW_FAILED   = "PLAN_REVIEW_FAILED"
    STAGE_EVAL_FAILED    = "STAGE_EVAL_FAILED"
    USER_GUIDANCE        = "USER_GUIDANCE"


# ---------------------------------------------------------------------------
# NextDecisionType / NextDecision
# ---------------------------------------------------------------------------

class NextDecisionType(str, Enum):
    TOOL_CALL            = "TOOL_CALL"
    FINAL_ANSWER         = "FINAL_ANSWER"
    CONTINUE             = "CONTINUE"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"


@dataclass(frozen=True)
class NextDecision:
    decision_type: NextDecisionType
    tool_calls: list[ToolCall] = field(default_factory=list)
    answer: str = ""
    message: str = ""
    assistant_message: LLMMessage | None = None
    raw_response: LLMResponse | None = None

@dataclass(frozen=True)
class Plan:
    id: PlanId
    task_id: TaskId
    step_list: list[PlanStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def step_count(self) -> int:
        return len(self.step_list)


@dataclass(frozen=True)
class Task:
    id: TaskId
    user_id: UserId
    description: str
    created_at: datetime
    task_type: str = ""
    complexity: str = ""
    required_tools: list[str] = field(default_factory=list)
    reasoning_depth: str = ""
    output_constraints: str = ""
    notes: str = ""
    related_user_preference_entries: list[RelatedPreferenceEntry] = field(default_factory=list)
    related_knowledge_entries: list[RelatedKnowledgeEntry] = field(default_factory=list)
    plan_id: PlanId | None = None
    task_feat: TaskFeature | None = None

@dataclass(frozen=True)
class TaskResult:
    task_id: TaskId
    succeeded: bool
    result: str
    error_reason: str
    delivered_at: datetime

@dataclass(frozen=True)
class PlanVersion:
    """Snapshot of a plan at a specific version, kept for audit and rollback."""
    plan: Plan
    version: int
    change_reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "StageStatus",
    "CheckpointEntry",
    "EvaluationRecord",
    "KnowledgeEntryStatus",
    "KnowledgeExtracted",
    "KnowledgeIndexed",
    "ProviderCapabilities",
    "RoutingDecision",
    "RelatedPreferenceEntry",
    "RelatedKnowledgeEntry",
    "PlanStep",
    "Plan",
    "TaskFeature",
    "PlanUpdateTrigger",
    "NextDecisionType",
    "NextDecision",
    "Task",
    "TaskResult",
    "PlanVersion",
]
