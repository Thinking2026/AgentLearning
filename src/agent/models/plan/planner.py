from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.domain import AggregateRoot
from schemas.ids import PlanId, PlanStepId, TaskId
from schemas.task import PlanStep, PlanUpdateTrigger, TaskFeature
from schemas.types import LLMMessage, LLMRequest

from agent.events import TaskPlanFinalized, TaskPlanRenewal, TaskPlanRevised

if TYPE_CHECKING:
    from llm.llm_gateway import LLMGateway
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader


# ---------------------------------------------------------------------------
# Aggregate root
# ---------------------------------------------------------------------------

class Planner(AggregateRoot):
    """Aggregate root responsible for task analysis and execution plan management."""

    def __init__(
        self,
        id: PlanId,
        task_id: TaskId,
        task_description: str,
        llm_gateway: LLMGateway,
        knowledge_loader: KnowledgeLoader | None,
    ) -> None:
        super().__init__()
        self.id = id
        self.task_id = task_id
        self.task_description = task_description
        self.task_feat: TaskFeature | None = None
        self.steps: list[PlanStep] = []
        self.version: int = 0
        self._llm_gateway = llm_gateway
        self._knowledge_loader = knowledge_loader

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
        """Create an empty Planner with injected dependencies."""
        plan_id = PlanId(str(uuid4()))
        return cls(
            id=plan_id,
            task_id=task_id,
            task_description=task_description,
            llm_gateway=llm_gateway,
            knowledge_loader=knowledge_loader,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def build_plan(self, task_description: str) -> DTO:
        try:
            self.analyze()
            plan_retries = 0
            feedback = ""
            clarification = ""
            while plan_retries <= self._max_plan_retries:
                self.build_plan(feedback, clarification)
                review = self._quality_evaluator.review_plan()
                if not review.passed:
                    plan_retries += 1
                    if review.need_user_clarification:
                        self._stage_executor.pause(review.clarification_question)
                        self._resume_event.wait()
                        self._resume_event.clear()
                        clarification = self._clarification or ""
                        self._clarification = None
                        feedback=review.feedback,
                        clarification=clarification,
                    else:
                        feedback=review.feedback,
                else:
                    #success
                    return     
        except Exception as exc:
            return self._failed_result(task_id, f"Plan build failed: {exc}")    
        finally:
            return self._failed_result(task_id, f"Plan build failed: {exc}")    

    def analyze(self) -> Planner:
        """Call LLM to analyze the task and fill self.analysis."""
        prompt = (
            f"Analyze the following task and return a JSON object with these fields:\n"
            f"- task_type: string (e.g. 'data_analysis', 'code_generation', 'research')\n"
            f"- complexity: string ('simple', 'medium', or 'complex')\n"
            f"- required_tools: list of tool name strings\n"
            f"- estimated_steps: integer\n"
            f"- notes: string with constraints, risks, or prerequisites\n"
            f"- preferred_scenarios: list of scenario strings that best match this task "
            f"(choose from: code_generation, math, reasoning, analysis, research, writing, "
            f"general, long_document, summarization, document_qa, chinese_language, multimodal, tool_use, data_analysis)\n"
            f"- required_strengths: list of capability strings this task demands "
            f"(choose from: code, math, long_context, tool_use, instruction_following, "
            f"general_purpose, cost_efficiency, chinese_language, document_understanding, ultra_long_context)\n"
            f"- min_context_size: integer — minimum context window (tokens) needed; 0 if unknown\n"
            f"- prefer_low_cost: boolean — true if cost matters more than quality\n"
            f"- prefer_low_latency: boolean — true if response speed is critical\n\n"
            f"Task: {self.task_description}\n\n"
            f"Respond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        self.task_feat = self._parse_analysis(response.assistant_message.content)
        return self

    def _build_plan_impl(self, knowledge_hint: str = "") -> None:
        """Call LLM to create execution steps, fill self.steps, version=1."""
        knowledge_context = ""
        if self._knowledge_loader is not None:
            entries = self._knowledge_loader.load(self.task_description)
            if entries:
                snippets = "\n".join(f"- {e.content}" for e in entries)
                knowledge_context = f"\nRelevant prior knowledge:\n{snippets}\n"

        if knowledge_hint:
            knowledge_context += f"\nAdditional hint: {knowledge_hint}\n"

        analysis_context = ""
        if self.task_feat is not None:
            analysis_context = (
                f"\nTask analysis: type={self.task_feat.task_type}, "
                f"complexity={self.task_feat.complexity}, "
                f"estimated_steps={self.task_feat.estimated_steps}\n"
            )

        prompt = (
            f"Create an execution plan for the following task.\n"
            f"Task: {self.task_description}\n"
            f"{analysis_context}"
            f"{knowledge_context}\n"
            f"Return a JSON array of steps. Each step must have:\n"
            f"- goal: string (what this step achieves, used as evaluation criterion)\n"
            f"- description: string (how to execute this step)\n\n"
            f"Respond with only a valid JSON array."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        self.steps = self._parse_steps(response.assistant_message.content)
        self.version = 1
        self._record(
            TaskPlanFinalized(
                event_type="",
                aggregate_id=self.id,
                task_id=self.task_id,
                plan_id=self.id,
            )
        )

    def renew(self, trigger: PlanUpdateTrigger, feedback: str = "", clarification: str = "") -> None:
        """Rebuild all steps from scratch; version+1."""
        feedback_context = f"\nFeedback: {feedback}\n" if feedback else ""
        clarification_context = f"\nUser clarification: {clarification}\n" if clarification else ""
        prompt = (
            f"The current execution plan needs to be completely rebuilt.\n"
            f"Task: {self.task_description}\n"
            f"Trigger: {trigger.value}\n"
            f"{feedback_context}"
            f"{clarification_context}\n"
            f"Return a JSON array of steps. Each step must have:\n"
            f"- goal: string\n"
            f"- description: string\n\n"
            f"Respond with only a valid JSON array."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        self.steps = self._parse_steps(response.assistant_message.content)
        self.version += 1
        self._record(
            TaskPlanRenewal(
                event_type="",
                aggregate_id=self.id,
                task_id=self.task_id,
                plan_id=self.id,
                trigger=trigger.value,
            )
        )

    def revise(
        self,
        step_id: PlanStepId,
        trigger: PlanUpdateTrigger,
        feedback: str = "",
    ) -> None:
        """Update a single step's goal/description; version+1."""
        target = self.get_step(step_id)
        if target is None:
            return

        feedback_context = f"\nFeedback: {feedback}\n" if feedback else ""
        prompt = (
            f"Revise the following execution step.\n"
            f"Task: {self.task_description}\n"
            f"Trigger: {trigger.value}\n"
            f"Current step goal: {target.goal}\n"
            f"Current step description: {target.description}\n"
            f"{feedback_context}\n"
            f"Return a JSON object with:\n"
            f"- goal: string\n"
            f"- description: string\n\n"
            f"Respond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        updated = self._parse_single_step(
            response.assistant_message.content,
            step_id=step_id,
            order=target.order,
        )
        self.steps = [updated if s.id == step_id else s for s in self.steps]
        self.version += 1
        self._record(
            TaskPlanRevised(
                event_type="",
                aggregate_id=self.id,
                task_id=self.task_id,
                plan_id=self.id,
                step_id=step_id,
                trigger=trigger.value,
            )
        )

    def get_step(self, step_id: PlanStepId) -> PlanStep | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_step_by_order(self, order: int) -> PlanStep | None:
        for step in self.steps:
            if step.order == order:
                return step
        return None

    def total_steps(self) -> int:
        return len(self.steps)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_analysis(self, content: str) -> TaskFeature:
        try:
            data = json.loads(_extract_json(content))
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
            return TaskFeature(
                task_type="general",
                complexity="medium",
                required_tools=[],
                estimated_steps=1,
                notes="",
            )

    def _parse_steps(self, content: str) -> list[PlanStep]:
        try:
            data = json.loads(_extract_json(content))
            if not isinstance(data, list):
                raise ValueError("expected a JSON array")
            steps = []
            for i, item in enumerate(data):
                steps.append(
                    PlanStep(
                        id=PlanStepId(str(uuid4())),
                        goal=str(item.get("goal", f"Step {i}")),
                        description=str(item.get("description", "")),
                        order=i,
                    )
                )
            return steps if steps else self._fallback_steps()
        except Exception:
            return self._fallback_steps()

    def _parse_single_step(
        self,
        content: str,
        step_id: PlanStepId,
        order: int,
    ) -> PlanStep:
        try:
            data = json.loads(_extract_json(content))
            return PlanStep(
                id=step_id,
                goal=str(data.get("goal", self.task_description)),
                description=str(data.get("description", self.task_description)),
                order=order,
            )
        except Exception:
            return PlanStep(
                id=step_id,
                goal=self.task_description,
                description=self.task_description,
                order=order,
            )

    def _fallback_steps(self) -> list[PlanStep]:
        return [
            PlanStep(
                id=PlanStepId(str(uuid4())),
                goal=self.task_description,
                description=self.task_description,
                order=0,
            )
        ]


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, return raw JSON string."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first and last fence lines
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        return "\n".join(inner)
    return text
