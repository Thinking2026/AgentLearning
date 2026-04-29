from __future__ import annotations

from typing import NewType

# Task Management Context
TaskId = NewType("TaskId", str)
TaskPlanId = NewType("TaskPlanId", str)
TaskExecutionId = NewType("TaskExecutionId", str)
TaskStepId = NewType("TaskStepId", str)
SnapshotId = NewType("SnapshotId", str)

# Agent Execution Context
StepExecutionId = NewType("StepExecutionId", str)

# Knowledge Flywheel Context
KnowledgeEntryId = NewType("KnowledgeEntryId", str)

# Tool Execution Context
ToolCallId = NewType("ToolCallId", str)

# User / Preference Context
UserId = NewType("UserId", str)
