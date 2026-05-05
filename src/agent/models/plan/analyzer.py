from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.ids import TaskId, UserId
from schemas.task import (
    RelatedKnowledgeEntry,
    RelatedUserPreferenceEntry,
    ReasoningType,
    Task,
    TaskComplexity,
    TaskStatus,
)
from schemas.types import LLMMessage, UnifiedLLMRequest
from utils.log.log import Logger, zap

if TYPE_CHECKING:
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.personality.user_preference import PersonalityManager
    from llm.llm_gateway import LLMGateway
    from tools.tool_registry import ToolRegistry

_ANALYZE_SYSTEM_PROMPT = """\
You are a task analysis assistant. Given a task description and a list of available tools, \
extract structured features from the task.

Return a JSON object with the following keys:
  - "task_type": string — short category label (e.g. "data_analysis", "code_generation", "search")
  - "intent": string — one sentence describing the user's goal
  - "complexity_level": integer 1-5 — difficulty (1=trivial, 5=very complex)
  - "complexity_features": array of strings — characteristics that justify the complexity level
  - "complexity_use_cases": array of strings — typical tasks at this complexity level
  - "required_tools": array of strings — tool names from the provided list that are needed
  - "reasoning_depth": string — either "single-step reasoning" or "multi-step reasoning"
  - "output_constraints": string — any format or length constraints on the output (empty string if none)
  - "notes": string — any other relevant observations (empty string if none)

Respond with only valid JSON. No markdown fences."""


class AnalysisError(Exception):
    """Raised when task analysis fails."""


class Analyzer:
    """Extracts task features via LLM and enriches the Task with knowledge and preferences."""

    def analyze(
        self,
        task_description: str,
        llm_gateway: LLMGateway,
        knowledge_loader: KnowledgeLoader,
        personality_manager: PersonalityManager,
        tool_registry: ToolRegistry,
    ) -> Task:
        logger = Logger.get_instance()

        tool_names = [schema["function"]["name"] for schema in tool_registry.get_tool_schemas()]
        features = self._extract_features(task_description, tool_names, llm_gateway)

        task = Task(
            id=TaskId(str(uuid4())),
            user_id=UserId("unknown"),
            description=task_description,
            created_at=datetime.now(timezone.utc),
            status=TaskStatus.CREATED,
            task_type=features.get("task_type", ""),
            intent=features.get("intent", ""),
            complexity=TaskComplexity(
                level=int(features.get("complexity_level", 2)),
                features=list(features.get("complexity_features", [])),
                use_cases=list(features.get("complexity_use_cases", [])),
            ),
            required_tools=list(features.get("required_tools", [])),
            reasoning_depth=_parse_reasoning_depth(features.get("reasoning_depth", "")),
            output_constraints=features.get("output_constraints", ""),
            notes=features.get("notes", ""),
        )

        preference_entries = personality_manager.query_related_user_preference(task, llm_gateway)
        knowledge_entries = knowledge_loader.query_related_knowledge(task, llm_gateway)

        related_preferences = [
            RelatedUserPreferenceEntry(entry=e, confidence=1.0)
            for e in (preference_entries or [])
        ]
        related_knowledge = [
            RelatedKnowledgeEntry(entry=e, confidence=1.0)
            for e in (knowledge_entries or [])
        ]

        enriched_task = Task(
            id=task.id,
            user_id=task.user_id,
            description=task.description,
            created_at=task.created_at,
            status=task.status,
            task_type=task.task_type,
            intent=task.intent,
            complexity=task.complexity,
            required_tools=task.required_tools,
            reasoning_depth=task.reasoning_depth,
            output_constraints=task.output_constraints,
            notes=task.notes,
            related_user_preference_entries=related_preferences,
            related_knowledge_entries=related_knowledge,
        )

        logger.info(
            "Task analysis complete",
            zap.any("task_id", enriched_task.id),
            zap.any("task_type", enriched_task.task_type),
            zap.any("complexity_level", enriched_task.complexity.level),
            zap.any("required_tools", enriched_task.required_tools),
            zap.any("preference_count", len(related_preferences)),
            zap.any("knowledge_count", len(related_knowledge)),
        )
        return enriched_task

    def _extract_features(
        self,
        task_description: str,
        tool_names: list[str],
        llm_gateway: LLMGateway,
    ) -> dict:
        tools_block = ", ".join(tool_names) if tool_names else "(none)"
        prompt = (
            f"Task description:\n{task_description}\n\n"
            f"Available tools: {tools_block}"
        )
        response = llm_gateway.generate(
            UnifiedLLMRequest(
                messages=[LLMMessage(role="user", content=prompt)],
                system_prompt=_ANALYZE_SYSTEM_PROMPT,
                max_tokens=512,
                temperature=0.0,
            )
        )
        content = response.assistant_message.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
            content = "\n".join(inner)
        try:
            return json.loads(content)
        except Exception as exc:
            raise AnalysisError(f"Failed to parse LLM analysis response: {exc}") from exc


def _parse_reasoning_depth(value: str) -> ReasoningType:
    if value == ReasoningType.MULTI_STEP.value:
        return ReasoningType.MULTI_STEP
    return ReasoningType.SINGLE_STEP
