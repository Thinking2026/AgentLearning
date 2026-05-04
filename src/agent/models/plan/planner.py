from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.domain import AggregateRoot
from schemas.ids import PlanId, PlanStepId, TaskId
from schemas.task import (
    Plan,
    PlanChangeReason,
    PlanStep,
    PlanUpdateTrigger,
    PlanVersion,
    TaskFeature,
)
from schemas.types import LLMMessage, LLMRequest

from agent.events import TaskPlanFinalized, TaskPlanRevised, TaskPlanRenewal

if TYPE_CHECKING:
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from llm.llm_gateway import LLMGateway


def _new_plan_id() -> PlanId:
    return PlanId(f"plan_{uuid4().hex}")


def _new_step_id() -> PlanStepId:
    return PlanStepId(f"step_{uuid4().hex}")


def _parse_plan_steps(content: str) -> list[PlanStep]:
    """Parse LLM JSON response into PlanStep list."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        content = "\n".join(inner)
    try:
        data = json.loads(content)
        steps_data = data if isinstance(data, list) else data.get("steps", [])
        steps = []
        for i, s in enumerate(steps_data):
            steps.append(PlanStep(
                id=_new_step_id(),
                order=i,
                goal=str(s.get("goal", "")),
                description=str(s.get("description", "")),
                key_results=list(s.get("key_results", [])),
            ))
        return steps
    except Exception:
        return []


def _parse_task_feature(content: str) -> TaskFeature | None:
    """Parse LLM JSON response into TaskFeature."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        content = "\n".join(inner)
    try:
        data = json.loads(content)
        return TaskFeature(
            task_type=str(data.get("task_type", "general")),
            complexity=str(data.get("complexity", "medium")),
            required_tools=list(data.get("required_tools", [])),
            estimated_steps=int(data.get("estimated_steps", 1)),
            notes=str(data.get("notes", "")),
            preferred_scenarios=list(data.get("preferred_scenarios", [])),
            required_strengths=list(data.get("required_strengths", [])),
            min_context_size=int(data.get("min_context_size", 0)),
            prefer_low_cost=bool(data.get("prefer_low_cost", False)),
            prefer_low_latency=bool(data.get("prefer_low_latency", False)),
        )
    except Exception:
        return None


class Planner:
    def __init__(
        self,
        task_id: TaskId,
        task_description: str,
        llm_gateway: LLMGateway,
        knowledge_loader: KnowledgeLoader | None = None,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.task_description = task_description
        self._llm_gateway = llm_gateway
        self._knowledge_loader = knowledge_loader

        self.id: PlanId = _new_plan_id()
        self.version: int = 0
        self.task_feat: TaskFeature | None = None

        self._current_plan: Plan | None = None
        self._history: list[PlanVersion] = []

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        task_id: TaskId,
        task_description: str,
        llm_gateway: LLMGateway,
        knowledge_loader: KnowledgeLoader | None = None,
    ) -> Planner:
        return cls(
            task_id=task_id,
            task_description=task_description,
            llm_gateway=llm_gateway,
            knowledge_loader=knowledge_loader,
        )

    # ------------------------------------------------------------------
    # Properties consumed by Pipeline / QualityEvaluator
    # ------------------------------------------------------------------

    @property
    def steps(self) -> list[PlanStep]:
        if self._current_plan is None:
            return []
        return list(self._current_plan.step_list)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(self) -> TaskFeature | None:
        """Extract task features via LLM and cache as self.task_feat."""
        knowledge_context = self._build_knowledge_context()
        prompt = (
            f"Analyze the following task and return a JSON object describing its features.\n"
            f"Task: {self.task_description}\n"
            f"{knowledge_context}"
            f"\nReturn a JSON object with these fields:\n"
            f"- task_type: string (e.g. 'data_analysis', 'code_generation', 'question_answering')\n"
            f"- complexity: string ('simple', 'medium', or 'complex')\n"
            f"- required_tools: list of tool names likely needed\n"
            f"- estimated_steps: integer number of steps needed\n"
            f"- notes: string with any constraints or risks\n"
            f"- preferred_scenarios: list of scenario keywords\n"
            f"- required_strengths: list of capability keywords\n"
            f"- min_context_size: integer (0 if no special requirement)\n"
            f"- prefer_low_cost: boolean\n"
            f"- prefer_low_latency: boolean\n"
            f"\nRespond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        self.task_feat = _parse_task_feature(response.assistant_message.content)
        return self.task_feat

    # ------------------------------------------------------------------
    # Plan building
    # ------------------------------------------------------------------

    def _build_plan_impl(self, feedback: str = "", clarification: str = "") -> Plan:
        """Call LLM to generate a plan and store it as the current plan."""
        knowledge_context = self._build_knowledge_context()
        feedback_section = f"\nPrevious plan feedback to address:\n{feedback}\n" if feedback else ""
        clarification_section = f"\nUser clarification:\n{clarification}\n" if clarification else ""

        prompt = (
            f"Create a step-by-step execution plan for the following task.\n"
            f"Task: {self.task_description}\n"
            f"{knowledge_context}"
            f"{feedback_section}"
            f"{clarification_section}"
            f"\nReturn a JSON array of step objects. Each step must have:\n"
            f"- goal: string (what this step achieves)\n"
            f"- description: string (how to accomplish it)\n"
            f"- key_results: list of strings (concrete outputs to produce)\n"
            f"\nRespond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        steps = _parse_plan_steps(response.assistant_message.content)
        if not steps:
            steps = [PlanStep(
                id=_new_step_id(),
                order=0,
                goal="Complete the task",
                description=self.task_description,
                key_results=["Task completed"],
            )]

        plan = Plan(
            id=self.id,
            task_id=self.task_id,
            step_list=steps,
            created_at=datetime.now(timezone.utc),
        )
        self._current_plan = plan
        self.version += 1
        self._record(TaskPlanFinalized(
            event_type="",
            aggregate_id=str(self.task_id),
            task_id=self.task_id,
            plan_id=self.id,
        ))
        return plan

    # ------------------------------------------------------------------
    # Public plan operations
    # ------------------------------------------------------------------

    def make_plan(self) -> Plan:
        """Build the initial plan."""
        return self._build_plan_impl()

    def renew(
        self,
        trigger: PlanUpdateTrigger,
        feedback: str = "",
        clarification: str = "",
    ) -> Plan:
        """Rebuild the entire plan, archiving the current one as history."""
        if self._current_plan is not None:
            reason = _trigger_to_change_reason(trigger)
            self._history.append(PlanVersion(
                plan=self._current_plan,
                version=self.version,
                change_reason=reason,
            ))
        self.id = _new_plan_id()
        self._record(TaskPlanRenewal(
            event_type="",
            aggregate_id=str(self.task_id),
            task_id=self.task_id,
            plan_id=self.id,
            trigger=trigger.value,
        ))
        return self._build_plan_impl(feedback=feedback, clarification=clarification)

    def revise(
        self,
        step_id: PlanStepId,
        trigger: PlanUpdateTrigger,
        feedback: str = "",
    ) -> PlanStep:
        """Revise a single step in the current plan via LLM."""
        step = self.get_step(step_id)
        if step is None or self._current_plan is None:
            raise ValueError(f"Step {step_id} not found in current plan")

        prompt = (
            f"Revise the following execution step based on the feedback.\n"
            f"Task: {self.task_description}\n"
            f"Current step goal: {step.goal}\n"
            f"Current step description: {step.description}\n"
            f"Feedback: {feedback}\n"
            f"\nReturn a JSON object with:\n"
            f"- goal: string\n"
            f"- description: string\n"
            f"- key_results: list of strings\n"
            f"\nRespond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        new_step = _parse_revised_step(response.assistant_message.content, step)
        self._replace_step(step_id, new_step)
        self._record(TaskPlanRevised(
            event_type="",
            aggregate_id=str(self.task_id),
            task_id=self.task_id,
            plan_id=self.id,
            step_id=step_id,
            trigger=trigger.value,
        ))
        return new_step

    # ------------------------------------------------------------------
    # Step accessors
    # ------------------------------------------------------------------

    def get_step(self, step_id: PlanStepId) -> PlanStep | None:
        if self._current_plan is None:
            return None
        for s in self._current_plan.step_list:
            if s.id == step_id:
                return s
        return None

    def get_step_by_order(self, order: int) -> PlanStep | None:
        if self._current_plan is None:
            return None
        for s in self._current_plan.step_list:
            if s.order == order:
                return s
        return None

    def total_steps(self) -> int:
        if self._current_plan is None:
            return 0
        return self._current_plan.step_count

    def get_plan(self) -> Plan | None:
        return self._current_plan

    def get_history(self) -> list[PlanVersion]:
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replace_step(self, step_id: PlanStepId, new_step: PlanStep) -> None:
        assert self._current_plan is not None
        updated = [new_step if s.id == step_id else s for s in self._current_plan.step_list]
        self._current_plan = Plan(
            id=self._current_plan.id,
            task_id=self._current_plan.task_id,
            step_list=updated,
            created_at=self._current_plan.created_at,
        )

    def _build_knowledge_context(self) -> str:
        if self._knowledge_loader is None:
            return ""
        entries = self._knowledge_loader.load(self.task_description, top_k=3)
        if not entries:
            return ""
        snippets = "\n".join(f"- {e.content}" for e in entries)
        return f"\nRelevant knowledge:\n{snippets}\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trigger_to_change_reason(trigger: PlanUpdateTrigger) -> PlanChangeReason:
    mapping = {
        PlanUpdateTrigger.PLAN_REVIEW_FAILED:   PlanChangeReason.PLAN_EVALUATE_FAILED,
        PlanUpdateTrigger.QUALITY_CHECK_FAILED: PlanChangeReason.TASK_RESULT_EVALUATED,
        PlanUpdateTrigger.STAGE_EVAL_FAILED:    PlanChangeReason.STAGE_RESULT_EVALUATED,
        PlanUpdateTrigger.USER_GUIDANCE:        PlanChangeReason.STAGE_RESULT_EVALUATED,
    }
    return mapping.get(trigger, PlanChangeReason.PLAN_EVALUATE_FAILED)


def _parse_revised_step(content: str, fallback: PlanStep) -> PlanStep:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        content = "\n".join(inner)
    try:
        data = json.loads(content)
        return PlanStep(
            id=fallback.id,
            order=fallback.order,
            goal=str(data.get("goal", fallback.goal)),
            description=str(data.get("description", fallback.description)),
            key_results=list(data.get("key_results", fallback.key_results)),
        )
    except Exception:
        return fallback
