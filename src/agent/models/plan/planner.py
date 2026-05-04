from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.ids import PlanId, PlanStepId, TaskId
from schemas.task import (
    Plan,
    PlanChangeReason,
    PlanStep,
    PlanVersion,
    Task,
)
from schemas.types import LLMMessage, LLMRequest
from agent.models.evaluate.quality_evaluator import QualityEvaluator
from agent.events import TaskPlanFinalized, TaskPlanRevised, TaskPlanRenewal

if TYPE_CHECKING:
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from llm.llm_gateway import LLMGateway
