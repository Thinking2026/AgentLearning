from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from agent.application.driver import PipelineDriver
from schemas.ids import TaskId, UserId
from schemas.errors import AgentError
from schemas.task import Plan, PlanUpdateTrigger, Task, TaskResult
from schemas.types import ClientMessage

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
        pipeline_driver: PipelineDriver,
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
        self._pipeline_driver = pipeline_driver
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
        # Optional callback to push progress messages to the user
        self._send_to_user: Callable[[ClientMessage], None] | None = None

        # Wire checkpoint saving into the executor
        self._stage_executor.set_checkpoint_callback(self._save_checkpoint_async)

    # ------------------------------------------------------------------
    # Public control API (thread-safe, called from PipelineThread)
    # ------------------------------------------------------------------

    @property
    def stage_executor(self) -> StageExecutor:
        return self._stage_executor

    def set_send_to_user(self, callback: Callable[[ClientMessage], None]) -> None:
        self._send_to_user = callback

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        task_description: str,
    ) -> TaskResult:


    def continue_from_checkpoint(self, task_id: TaskId, cpt_id: CheckpointId) -> TaskResult:
        """Restore from the latest checkpoint and resume execution."""
        checkpoint = self._checkpoint_processor.restore_latest()
        if checkpoint is None:
            return self._failed_result(task_id, "No checkpoint found")

        self._stage_executor.replace_conversation_history(
            checkpoint.conversation_checkpoint
        )
        return self.run(task_id, task_description)

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
            self._send_to_user(ClientMessage(
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
