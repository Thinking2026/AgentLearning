from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── 分析 ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AnalysisReportProduced(DomainEvent):
    task_id: str = ""
    report_summary: str = ""


# ── 计划 ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionPlanFinalized(DomainEvent):
    task_id: str = ""
    plan_id: str = ""
    step_count: int = 0


# ── 用户交互 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UserSuggestionRequested(DomainEvent):
    task_id: str = ""
    question: str = ""


@dataclass(frozen=True)
class UserClarificationRequested(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    question: str = ""


# ── Task 生命周期 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskExecutionStarted(DomainEvent):
    task_id: str = ""


@dataclass(frozen=True)
class TaskResultProduced(DomainEvent):
    task_id: str = ""
    result: str = ""


@dataclass(frozen=True)
class TaskExecutionFailed(DomainEvent):
    task_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class TaskPaused(DomainEvent):
    task_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class TaskCancelled(DomainEvent):
    task_id: str = ""
    reason: str = ""


# ── Stage 生命周期 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StageExecutionStarted(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    plan_step_id: str = ""


@dataclass(frozen=True)
class StageResultProduced(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    result: str = ""


# ── LLM ───────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMResponseGenerated(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    model: str = ""
    content: str = ""


# ── 工具调用 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCallStarted(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallResultProduced(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    tool_name: str = ""
    result: Any = None


@dataclass(frozen=True)
class ToolCallFailed(DomainEvent):
    task_id: str = ""
    stage_id: str = ""
    tool_name: str = ""
    error: str = ""


__all__ = [
    "DomainEvent",
    "AnalysisReportProduced",
    "ExecutionPlanFinalized",
    "UserSuggestionRequested",
    "UserClarificationRequested",
    "TaskExecutionStarted",
    "TaskResultProduced",
    "TaskExecutionFailed",
    "TaskPaused",
    "TaskCancelled",
    "StageExecutionStarted",
    "StageResultProduced",
    "LLMResponseGenerated",
    "ToolCallStarted",
    "ToolCallResultProduced",
    "ToolCallFailed",
]
