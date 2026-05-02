from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from schemas.ids import TaskId
from schemas.errors import AgentError, LLMError, ErrorCategory
from schemas.task import PlanUpdateTrigger, StageStatus, Task, TaskResult

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
        llm_gateway: LLMGateway,
        max_plan_retries: int = 3,
        max_stage_retries: int = 2,
        max_quality_retries: int = 2,
    ) -> None:
        #操作聚合根
        self._planner = planner #内部和knowledge_loader交互，获取可能的背景知识
        self._stage_executor = stage_executor
        self._checkpoint_processor = checkpoint_processor
        self._knowledge_manager = knowledge_manager
        self._quality_evaluator = quality_evaluator
        self._model_selector = model_selector
        self._llm_gateway = llm_gateway

        #运行控制参数
        self._max_plan_retries = max_plan_retries
        self._max_stage_retries = max_stage_retries
        self._max_quality_retries = max_quality_retries

        #任务信息参数
        self._task: Task | None = None

        #与Agent Thread交互的状态
        self._cancelled = threading.Event()
        self._resume_event = threading.Event()
        self._guidance: str | None = None #TODO不需要存，直接使用
        self._clarification: str | None = None #TODO不需要存，直接使用

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
        self._task = Task(
            id=task_id,
            description=task_description,
            created_at=datetime.now(timezone.utc),
        )
        self._queue = []
        try:
            while quality_retries <= self._max_quality_retries:
                # Build plan
                plan = self._planner.build_plan(task_description, self._queue)
                self._task.task_feat = self._planner.task_feat.task_feat

                #model routing
                model_routing = self._model_selector.route(self._task.task_feat)

                # Execute stages
                result = self._stage_executor.execute(
                    task,
                    model_routing,
                )
                if result.succeeded:
                    # Quality check
                    qc = self._quality_evaluator.evaluate_task_result(final_result, self._queue)
                    if qc.passed:
                        result = final_result
                        self._extract_knowledge_async(task_id, task_description, final_result)
                        return TaskResult(
                            task_id=task_id,
                            succeeded=True,
                            result=final_result,
                            error_reason="",
                            delivered_at=datetime.now(timezone.utc),
                        )
                    else:
                        # Renew plan and retry
                        feedback=qc.feedback,
                        quality_retries += 1 
                else:
                    #用户取消，返回结果
        except AgentError as exc:
            return self._failed_result(task_id, f"Agent error: {exc.message}")
        finally:
            return self._failed_result(task_id, f"Agent error: {exc.message}")

    def run_from_checkpoint():
        return None
    
    def _update_reasoning_gateway(self, provider_name: str) -> None:
        """Build a new LLMGateway for the given provider and inject into ReasoningManager."""
        gateway = self._llm_gateway.for_provider(provider_name)
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
