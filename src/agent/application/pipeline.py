from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from schemas.ids import TaskId
from schemas.errors import AgentError, LLMError, ErrorCategory

from agent.models.executor.stage_executor import StageStatus
from agent.models.plan.planner import PlanUpdateTrigger

if TYPE_CHECKING:
    from agent.models.checkpoint.checkpoint_processor import CheckpointProcessor
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from agent.models.executor.stage_executor import StageExecutor
    from agent.models.knowledge.knowledge_manager import KnowledgeManager
    from agent.models.model_routing.provider_router import ModelSelector
    from agent.models.plan.planner import Planner
    from llm.registry import LLMProviderRegistry


@dataclass(frozen=True)
class TaskResult:
    task_id: TaskId
    succeeded: bool
    result: str
    error_reason: str
    delivered_at: datetime


class Pipeline:
    """Application-layer orchestrator for the full task lifecycle.

    Coordinates Planner, StageExecutor, QualityEvaluator, CheckpointProcessor,
    KnowledgeManager, and ModelSelector to execute a task end-to-end.
    """

    def __init__(
        self,
        planner: Planner,
        stage_executor: StageExecutor,
        checkpoint_processor: CheckpointProcessor,
        knowledge_manager: KnowledgeManager,
        quality_evaluator: QualityEvaluator,
        model_selector: ModelSelector,
        llm_provider_registry: LLMProviderRegistry,
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
        self._llm_provider_registry = llm_provider_registry
        self._max_plan_retries = max_plan_retries
        self._max_stage_retries = max_stage_retries
        self._max_quality_retries = max_quality_retries

        self._cancelled = threading.Event()
        self._guidance: str | None = None
        self._clarification: str | None = None
        self._resume_event = threading.Event()
        self._current_task_id: TaskId | None = None

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
        self._guidance = None
        self._clarification = None
        self._resume_event.clear()
        self._current_task_id = task_id

        # Build plan
        plan_retries = 0
        while plan_retries <= self._max_plan_retries:
            if self._cancelled.is_set():
                return self._cancelled_result(task_id)

            try:
                self._planner.analyze()
                self._planner.build_plan()
            except Exception as exc:
                return self._failed_result(task_id, f"Plan build failed: {exc}")

            # Review plan
            review = self._quality_evaluator.review_plan(self._planner)
            if not review.passed:
                plan_retries += 1
                if plan_retries > self._max_plan_retries:
                    return self._failed_result(task_id, "Max plan retries exceeded")
                if review.need_user_clarification:
                    self._stage_executor.pause(review.clarification_question)
                    self._resume_event.wait()
                    self._resume_event.clear()
                    clarification = self._clarification or ""
                    self._clarification = None
                    self._planner.renew(
                        PlanUpdateTrigger.PLAN_REVIEW_FAILED,
                        feedback=review.feedback,
                        clarification=clarification,
                    )
                else:
                    self._planner.renew(
                        PlanUpdateTrigger.PLAN_REVIEW_FAILED,
                        feedback=review.feedback,
                    )
                continue
            break

        #model routing
        analysis = self._planner.analysis
        routing = self._model_selector.route(analysis)
        provider_chain = [routing.primary, *routing.fallbacks]
        provider_index = 0
        self._update_reasoning_gateway(provider_chain[provider_index])

        # Execute stages
        quality_retries = 0
        while quality_retries <= self._max_quality_retries:
            if self._cancelled.is_set():
                return self._cancelled_result(task_id)

            final_result = self._execute_all_stages(
                task_id,
                provider_chain,
                provider_index,
            )
            if final_result is None:
                # Cancelled or terminated during stage execution
                return self._cancelled_result(task_id) if self._cancelled.is_set() \
                    else self._failed_result(task_id, "Stage execution failed")

            # Quality check
            qc = self._quality_evaluator.evaluate_task_result(final_result)
            if qc.passed:
                break
            quality_retries += 1
            if quality_retries > self._max_quality_retries:
                return self._failed_result(task_id, "Max quality retries exceeded")
            # Renew plan and retry
            self._planner.renew(
                PlanUpdateTrigger.QUALITY_CHECK_FAILED,
                feedback=qc.feedback,
            )
            self._stage_executor.reset_for_next_stage()

        # Async knowledge extraction (best-effort, non-blocking)
        self._extract_knowledge_async(task_id, task_description, final_result)

        return TaskResult(
            task_id=task_id,
            succeeded=True,
            result=final_result,
            error_reason="",
            delivered_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # User interaction entry points
    # ------------------------------------------------------------------

    def cancel(self, task_id: TaskId) -> None:
        """UC-2: User cancels the running task."""
        self._cancelled.set()
        self._stage_executor.interrupt("cancelled")

    def submit_guidance(self, task_id: TaskId, guidance: str) -> None:
        """UC-3: User submits guidance to redirect the current step."""
        self._guidance = guidance
        self._stage_executor.interrupt(guidance)

    def submit_clarification(self, task_id: TaskId, clarification: str) -> None:
        """UC-4: User provides clarification; resumes paused stage."""
        self._clarification = clarification
        self._resume_event.set()

    def resume(self, task_id: TaskId) -> None:
        """UC-5: User resumes after a B-class pause."""
        self._stage_executor.resume()
        self._resume_event.set()

    def restore_from_checkpoint(self, task_id: TaskId) -> None:
        """UC-6: Restore from the latest checkpoint."""
        entry = self._checkpoint_processor.restore_latest()
        if entry is not None:
            self._stage_executor.replace_conversation_history(entry.conversation_checkpoint)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_all_stages(
        self,
        task_id: TaskId,
        provider_chain: list[str],
        provider_index: int,
    ) -> str | None:
        """Execute all plan steps in order. Returns the last step result or None on failure."""
        last_result = ""
        step_index = 0

        while step_index < self._planner.total_steps():
            if self._cancelled.is_set():
                return None

            step = self._planner.get_step_by_order(step_index)
            if step is None:
                break

            stage_retries = 0
            resume_existing_context = False
            while stage_retries <= self._max_stage_retries:
                if not resume_existing_context:
                    self._stage_executor.reset_for_next_stage()

                # Handle user guidance interrupt from previous iteration
                if self._guidance:
                    self._planner.revise(
                        step.id,
                        PlanUpdateTrigger.USER_GUIDANCE,
                        feedback=self._guidance,
                    )
                    self._guidance = None
                    step = self._planner.get_step_by_order(step_index)
                    if step is None:
                        break
                    resume_existing_context = False

                try:
                    stage = self._stage_executor.execute_stage(
                        task_id=task_id,
                        plan_step_id=step.id,
                        plan_step_goal=step.goal,
                        plan_step_description=step.description,
                        resume_existing_context=resume_existing_context,
                        provider_name=provider_chain[provider_index],
                    )
                    resume_existing_context = False
                except LLMError as exc:
                    if exc.category in (
                        ErrorCategory.AUTH,
                        ErrorCategory.CONFIG,
                        ErrorCategory.RESPONSE,
                    ):
                        next_index = self._next_provider_index(provider_chain, provider_index)
                        if next_index is not None:
                            provider_index = next_index
                            self._update_reasoning_gateway(provider_chain[provider_index])
                            resume_existing_context = True
                            continue
                        return None
                    # B-class: pause and wait, then continue the same step context.
                    self._stage_executor.pause(exc.message)
                    self._resume_event.wait()
                    self._resume_event.clear()
                    resume_existing_context = True
                    continue
                except AgentError as exc:
                    return None

                if stage.status == StageStatus.INTERRUPTED:
                    # User guidance: revise step and retry
                    self._planner.revise(
                        step.id,
                        PlanUpdateTrigger.USER_GUIDANCE,
                        feedback=stage.interrupt_guidance,
                    )
                    self._guidance = None
                    step = self._planner.get_step_by_order(step_index)
                    resume_existing_context = False
                    stage_retries += 1
                    continue

                if stage.status == StageStatus.PAUSED:
                    # Wait for user clarification or resume, then continue this step.
                    self._resume_event.wait()
                    self._resume_event.clear()
                    if self._clarification:
                        self._stage_executor.append_user_clarification(self._clarification)
                        self._clarification = None
                    resume_existing_context = True
                    continue

                if stage.status == StageStatus.FAILED:
                    resume_existing_context = False
                    stage_retries += 1
                    if stage_retries > self._max_stage_retries:
                        # Revise plan step and retry from this step
                        self._planner.revise(
                            step.id,
                            PlanUpdateTrigger.STAGE_INFEASIBLE,
                        )
                        step = self._planner.get_step_by_order(step_index)
                        stage_retries = 0
                    continue

                # Stage completed successfully
                last_result = stage.result

                # Evaluate step result
                eval_record = self._quality_evaluator.evaluate_step_result(step, stage.result)
                if not eval_record.passed:
                    self._planner.revise(
                        step.id,
                        PlanUpdateTrigger.STAGE_EVAL_FAILED,
                        feedback=eval_record.feedback,
                    )
                    resume_existing_context = False
                    stage_retries += 1
                    if stage_retries > self._max_stage_retries:
                        # Move on despite failure
                        break
                    continue

                # Save checkpoint asynchronously
                self._stage_executor.archive_current_stage_context()
                self._save_checkpoint_async(task_id, step_index)
                break

            step_index += 1

        return last_result

    def _update_reasoning_gateway(self, provider_name: str) -> None:
        """Build a new LLMGateway for the given provider and inject into ReasoningManager."""
        gateway = self._llm_provider_registry.build_gateway(provider_name)
        self._stage_executor.set_llm_gateway(gateway)

    @staticmethod
    def _next_provider_index(provider_chain: list[str], current_index: int) -> int | None:
        next_index = current_index + 1
        if next_index >= len(provider_chain):
            return None
        return next_index

    def _save_checkpoint_async(self, task_id: TaskId, stage_order: int) -> None:
        conversation = self._stage_executor.get_conversation_history()
        plan_id = self._planner.id

        def _save() -> None:
            try:
                self._checkpoint_processor.save(plan_id, stage_order, conversation)
            except Exception:
                pass

        threading.Thread(target=_save, daemon=True).start()

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
