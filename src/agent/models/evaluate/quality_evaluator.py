from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from schemas.domain import AggregateRoot
from schemas.ids import PlanStepId, TaskId
from schemas.task import EvaluationRecord, PlanStep
from schemas.types import LLMMessage, LLMRequest

from agent.events import (
    PlanReviewFailed,
    PlanReviewPassed,
    StepResultEvaluationFailed,
    StepResultEvaluationSucceeded,
    TaskQualityCheckFailed,
    TaskQualityCheckPassed,
)

if TYPE_CHECKING:
    from agent.models.plan.planner import Planner
    from llm.llm_gateway import LLMGateway


# ---------------------------------------------------------------------------
# Aggregate root
# ---------------------------------------------------------------------------

class QualityEvaluator(AggregateRoot):
    """Aggregate root for evaluating task results, step results, and execution plans."""

    def __init__(
        self,
        task_id: TaskId,
        task_description: str,
        llm_gateway: LLMGateway,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.task_description = task_description
        self.evaluation_history: list[EvaluationRecord] = []
        self._llm_gateway = llm_gateway

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_task(
        cls,
        task_id: TaskId,
        task_description: str,
        llm_gateway: LLMGateway,
    ) -> QualityEvaluator:
        return cls(
            task_id=task_id,
            task_description=task_description,
            llm_gateway=llm_gateway,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def evaluate_task_result(self, result: str) -> EvaluationRecord:
        """Evaluate the overall task result against the task description.

        Publishes TaskQualityCheckPassed or TaskQualityCheckFailed.
        """
        prompt = (
            f"Evaluate whether the following result satisfies the task requirements.\n"
            f"Task: {self.task_description}\n"
            f"Result: {result}\n\n"
            f"Return a JSON object with:\n"
            f"- passed: boolean\n"
            f"- feedback: string (improvement suggestions if not passed, empty string if passed)\n\n"
            f"Respond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        passed, feedback = _parse_evaluation(response.assistant_message.content)
        record = EvaluationRecord(
            target_type="task",
            target_id=str(self.task_id),
            passed=passed,
            feedback=feedback,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.evaluation_history.append(record)

        if passed:
            self._record(
                TaskQualityCheckPassed(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                )
            )
        else:
            self._record(
                TaskQualityCheckFailed(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    feedback=feedback,
                )
            )
        return record

    def evaluate_step_result(self, step: PlanStep, result: str) -> EvaluationRecord:
        """Evaluate a step result against the step goal.

        Publishes StepResultEvaluationSucceeded or StepResultEvaluationFailed.
        """
        prompt = (
            f"Evaluate whether the following result achieves the step goal.\n"
            f"Step goal: {step.goal}\n"
            f"Step description: {step.description}\n"
            f"Result: {result}\n\n"
            f"Return a JSON object with:\n"
            f"- passed: boolean\n"
            f"- feedback: string (improvement suggestions if not passed, empty string if passed)\n\n"
            f"Respond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        passed, feedback = _parse_evaluation(response.assistant_message.content)
        record = EvaluationRecord(
            target_type="step",
            target_id=str(step.id),
            passed=passed,
            feedback=feedback,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.evaluation_history.append(record)

        if passed:
            self._record(
                StepResultEvaluationSucceeded(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    step_id=step.id,
                )
            )
        else:
            self._record(
                StepResultEvaluationFailed(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    step_id=step.id,
                    feedback=feedback,
                )
            )
        return record

    def review_plan(self, planner: Planner) -> EvaluationRecord:
        """Review the execution plan for feasibility and completeness.

        Publishes PlanReviewPassed or PlanReviewFailed.
        """
        steps_text = "\n".join(
            f"  Step {s.order}: goal={s.goal}, description={s.description}"
            for s in planner.steps
        )
        prompt = (
            f"Review the following execution plan for the given task.\n"
            f"Task: {self.task_description}\n"
            f"Plan (version {planner.version}):\n{steps_text}\n\n"
            f"Return a JSON object with:\n"
            f"- passed: boolean (true if the plan is feasible and likely to achieve the task)\n"
            f"- feedback: string (issues and suggestions if not passed, empty string if passed)\n"
            f"- need_user_clarification: boolean (true if the plan cannot proceed without additional information from the user)\n"
            f"- clarification_question: string (the specific question to ask the user; empty string if need_user_clarification is false)\n\n"
            f"Respond with only valid JSON."
        )
        response = self._llm_gateway.generate(
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        )
        passed, feedback, need_clarification, clarification_question = _parse_plan_review(response.assistant_message.content)
        record = EvaluationRecord(
            target_type="plan",
            target_id=str(planner.id),
            passed=passed,
            feedback=feedback,
            evaluated_at=datetime.now(timezone.utc),
            need_user_clarification=need_clarification,
            clarification_question=clarification_question,
        )
        self.evaluation_history.append(record)

        if passed:
            self._record(
                PlanReviewPassed(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    plan_id=planner.id,
                )
            )
        else:
            self._record(
                PlanReviewFailed(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    plan_id=planner.id,
                    feedback=feedback,
                )
            )
        return record

    def get_latest_task_evaluation(self) -> EvaluationRecord | None:
        for record in reversed(self.evaluation_history):
            if record.target_type == "task":
                return record
        return None

    def get_latest_step_evaluation(self, step_id: PlanStepId) -> EvaluationRecord | None:
        for record in reversed(self.evaluation_history):
            if record.target_type == "step" and record.target_id == str(step_id):
                return record
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_plan_review(content: str) -> tuple[bool, str, bool, str]:
    """Parse plan review response. Returns (passed, feedback, need_user_clarification, clarification_question)."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        content = "\n".join(inner)
    try:
        data = json.loads(content)
        passed = bool(data.get("passed", False))
        feedback = str(data.get("feedback", ""))
        need_clarification = bool(data.get("need_user_clarification", False))
        clarification_question = str(data.get("clarification_question", ""))
        return passed, feedback, need_clarification, clarification_question
    except Exception:
        return True, "", False, ""


def _parse_evaluation(content: str) -> tuple[bool, str]:
    """Parse LLM evaluation response. Returns (passed, feedback)."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        content = "\n".join(inner)
    try:
        data = json.loads(content)
        passed = bool(data.get("passed", False))
        feedback = str(data.get("feedback", ""))
        return passed, feedback
    except Exception:
        # Fallback: treat any response as passed with no feedback
        return True, ""
