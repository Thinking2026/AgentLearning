from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from schemas.ids import TaskId
from schemas.errors import AgentError
from schemas.task import PlanUpdateTrigger, Task, TaskResult
from schemas.types import UIMessage

if TYPE_CHECKING:
    from agent.models.checkpoint.checkpoint_processor import CheckpointProcessor
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from agent.models.executor.stage_executor import StageExecutor
    from agent.models.knowledge.knowledge_manager import KnowledgeManager
    from agent.models.model_routing.provider_router import ModelSelector
    from agent.models.plan.planner import Planner
    from llm.llm_gateway import LLMGateway


class Pipeline:
    """Application-layer orchestrator for the full task lifecycle.

    Implements the three-level loop from TD.md:
      Task Level  → plan → execute stages → quality check
      Stage Level → handled by StageExecutor.execute()
      Reasoning   → handled by StageExecutor._execute_stage()

    User signals (cancel / guidance / clarification / resume) are delivered via
    the public control methods, which are safe to call from any thread.
    """

    def __init__(
        self,
        planner: Planner,
        stage_executor: StageExecutor,
        checkpoint_processor: CheckpointProcessor,
        knowledge_manager: KnowledgeManager,
        quality_evaluator: QualityEvaluator,
        model_selector: ModelSelector,
        llm_gateway: LLMGateway,
        max_plan_retries: int = 3,
        max_stage_retries: int = 2,
        max_quality_retries: int = 2,
    ) -> None:
        self._planner = planner
        self._stage_executor = stage_executor
        self._checkpoint_processor = checkpoint_processor
        self._knowledge_manager = knowledge_manager
        self._quality_evaluator = quality_evaluator
        self._model_selector = model_selector
        self._llm_gateway = llm_gateway

        self._max_plan_retries = max_plan_retries
        self._max_stage_retries = max_stage_retries
        self._max_quality_retries = max_quality_retries

        self._task: Task | None = None

        # Cross-thread control signals
        self._cancelled = threading.Event()
        self._resume_event = threading.Event()
        self._guidance_event = threading.Event()
        self._clarification_event = threading.Event()
        self._guidance_text: str = ""
        self._clarification_text: str = ""

        # Optional callback to push progress messages to the user
        self._send_to_user: Callable[[UIMessage], None] | None = None

    # ------------------------------------------------------------------
    # Public control API (thread-safe, called from PipelineThread)
    # ------------------------------------------------------------------

    def set_send_to_user(self, callback: Callable[[UIMessage], None]) -> None:
        self._send_to_user = callback
        self._stage_executor.set_send_to_user(callback)

    def cancel(self) -> None:
        self._cancelled.set()
        self._stage_executor.cancel()

    def provide_guidance(self, guidance: str) -> None:
        self._guidance_text = guidance
        self._guidance_event.set()
        self._stage_executor.interrupt(guidance)

    def provide_clarification(self, clarification: str) -> None:
        self._clarification_text = clarification
        self._clarification_event.set()
        self._stage_executor.provide_clarification(clarification)

    def resume(self) -> None:
        self._resume_event.set()
        self._stage_executor.resume()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: TaskId,
        task_description: str,
    ) -> TaskResult:
        """Execute a task end-to-end and return the final TaskResult."""
        self._cancelled.clear()
        self._resume_event.clear()
        self._guidance_event.clear()
        self._clarification_event.clear()
        self._guidance_text = ""
        self._clarification_text = ""

        self._task = Task(
            id=task_id,
            description=task_description,
            created_at=datetime.now(timezone.utc),
        )

        try:
            return self._run_task(task_id, task_description)
        except AgentError as exc:
            return self._failed_result(task_id, f"Agent error: {exc.message}")
        except Exception as exc:
            return self._failed_result(task_id, f"Unexpected error: {exc}")

    def run_from_checkpoint(self, task_id: TaskId, task_description: str) -> TaskResult:
        """Restore from the latest checkpoint and resume execution."""
        checkpoint = self._checkpoint_processor.restore_latest()
        if checkpoint is None:
            return self._failed_result(task_id, "No checkpoint found")

        self._stage_executor.replace_conversation_history(
            checkpoint.conversation_checkpoint
        )
        return self.run(task_id, task_description)

    # ------------------------------------------------------------------
    # Internal task execution
    # ------------------------------------------------------------------

    def _run_task(self, task_id: TaskId, task_description: str) -> TaskResult:
        # 1. Analyze task
        self._planner.analyze()
        if self._cancelled.is_set():
            return self._cancelled_result(task_id)

        # 2. Model routing
        routing = self._model_selector.route(self._planner.task_feat)
        provider_chain = [routing.primary] + list(routing.fallbacks)

        self._notify_user(f"Task analysis complete. Planning with {routing.primary}.")

        # 3. Outer quality-retry loop
        quality_retries = 0
        plan_feedback = ""
        plan_clarification = ""

        while quality_retries <= self._max_quality_retries:
            if self._cancelled.is_set():
                return self._cancelled_result(task_id)

            # 3a. Plan loop (build + review)
            plan = self._build_reviewed_plan(
                task_id, plan_feedback, plan_clarification
            )
            if plan is None:
                return self._failed_result(task_id, "Failed to build a valid execution plan")

            if self._cancelled.is_set():
                return self._cancelled_result(task_id)

            self._notify_user(
                f"Execution plan ready ({len(self._planner.steps)} steps). Starting execution."
            )

            # 3b. Execute all stages
            result = self._stage_executor.execute(
                task=self._task,  # type: ignore[arg-type]
                planner=self._planner,
                provider_chain=provider_chain,
            )

            if self._cancelled.is_set():
                return self._cancelled_result(task_id)

            if result is None:
                return self._failed_result(task_id, "Stage execution failed")

            # 3c. Quality check
            qc = self._quality_evaluator.evaluate_task_result(result)
            if qc.passed:
                self._notify_user("Task completed successfully.")
                self._extract_knowledge_async(task_id, task_description, result)
                return TaskResult(
                    task_id=task_id,
                    succeeded=True,
                    result=result,
                    error_reason="",
                    delivered_at=datetime.now(timezone.utc),
                )

            # Quality check failed: renew plan and retry
            quality_retries += 1
            plan_feedback = qc.feedback
            plan_clarification = ""
            self._notify_user(
                f"Quality check failed (attempt {quality_retries}/{self._max_quality_retries}). "
                f"Replanning. Feedback: {qc.feedback}"
            )
            # Full context reset before replanning
            self._stage_executor.reset_for_next_stage()
            self._planner.renew(
                PlanUpdateTrigger.QUALITY_CHECK_FAILED,
                feedback=plan_feedback,
            )

        return self._failed_result(task_id, "Max quality retries exceeded")

    def _build_reviewed_plan(
        self,
        task_id: TaskId,
        initial_feedback: str,
        initial_clarification: str,
    ) -> object:
        """Build a plan and iterate through review until it passes or retries are exhausted."""
        plan_retries = 0
        feedback = initial_feedback
        clarification = initial_clarification

        # First build
        if feedback:
            self._planner.renew(PlanUpdateTrigger.PLAN_REVIEW_FAILED, feedback, clarification)
        else:
            self._planner._build_plan_impl()

        while plan_retries <= self._max_plan_retries:
            if self._cancelled.is_set():
                return None

            review = self._quality_evaluator.review_plan(self._planner)
            if review.passed:
                return self._planner

            plan_retries += 1
            if plan_retries > self._max_plan_retries:
                return None

            if review.need_user_clarification:
                # Ask user and wait
                question = review.clarification_question or "Please clarify the task requirements."
                self._notify_user(question)
                self._clarification_event.clear()
                self._clarification_event.wait()
                clarification = self._clarification_text
                self._clarification_event.clear()
                if self._cancelled.is_set():
                    return None
            else:
                clarification = ""

            feedback = review.feedback
            self._planner.renew(PlanUpdateTrigger.PLAN_REVIEW_FAILED, feedback, clarification)

        return None

    # ------------------------------------------------------------------
    # Async side-effects
    # ------------------------------------------------------------------

    def _extract_knowledge_async(
        self,
        task_id: TaskId,
        task_description: str,
        result: str,
    ) -> None:
        summary = f"Task: {task_description}\nResult: {result}"

        def _extract() -> None:
            try:
                self._knowledge_manager.extract_and_persist(summary)
            except Exception:
                pass

        threading.Thread(target=_extract, daemon=True).start()

    def _save_checkpoint_async(self, task_id: TaskId, stage_order: int) -> None:
        conversation = self._stage_executor.get_conversation_history()
        plan_id = self._planner.id

        def _save() -> None:
            try:
                self._checkpoint_processor.save(plan_id, stage_order, conversation)
            except Exception:
                pass

        threading.Thread(target=_save, daemon=True).start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _notify_user(self, message: str) -> None:
        if self._send_to_user and message:
            self._send_to_user(UIMessage(
                role="assistant",
                content=message,
                metadata={"source": "progress"},
            ))

    def _update_reasoning_gateway(self, provider_name: str) -> None:
        gateway = self._llm_gateway.for_provider(provider_name)
        self._stage_executor.set_llm_gateway(gateway)

    @staticmethod
    def _next_provider_index(provider_chain: list[str], current_index: int) -> int | None:
        next_index = current_index + 1
        return next_index if next_index < len(provider_chain) else None

    def _cancelled_result(self, task_id: TaskId) -> TaskResult:
        return TaskResult(
            task_id=task_id,
            succeeded=False,
            result="",
            error_reason="Task cancelled by user",
            delivered_at=datetime.now(timezone.utc),
        )

    def _failed_result(self, task_id: TaskId, reason: str) -> TaskResult:
        return TaskResult(
            task_id=task_id,
            succeeded=False,
            result="",
            error_reason=reason,
            delivered_at=datetime.now(timezone.utc),
        )
