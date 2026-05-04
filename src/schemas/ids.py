from __future__ import annotations

from typing import NewType

UserId           = NewType("UserId", str)
TaskId           = NewType("TaskId", str)
PlanId           = NewType("PlanId", str)
PlanStepId       = NewType("PlanStepId", str)
StageId          = NewType("StageId", str)
CheckpointId     = NewType("CheckpointId", str)
KnowledgeEntryId = NewType("KnowledgeEntryId", str)
