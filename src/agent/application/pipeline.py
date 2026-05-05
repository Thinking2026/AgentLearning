from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from agent.application.driver import PipelineDriver
from agent.events.events import (
    ExecutionPlanFinalized,
    TaskAnalysisCompleted,
    TaskExecutionFailed,
    TaskExecutionStarted,
    TaskResultProduced,
    UserSuggestionRequested,
)
from schemas.ids import CheckpointId, TaskId
from schemas.errors import AgentError
from schemas.task import Plan, Task, TaskResult
from schemas.types import ClientMessage

if TYPE_CHECKING:
    from agent.models.analysis.analyzer import Analyzer
    from agent.models.checkpoint.checkpoint_processor import CheckpointProcessor
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from agent.models.executor.stage_executor import StageExecutor
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.knowledge.knowledge_manager import KnowledgeManager
    from agent.models.model_routing.provider_router import ModelSelector
    from agent.models.personality.user_preference import PersonalityManager
    from agent.models.plan.planner import Planner
    from llm.llm_gateway import LLMGateway
    from tools.tool_registry import ToolRegistry


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
        analyzer: Analyzer,
        planner: Planner,
        pipeline_driver: PipelineDriver,
        stage_executor: StageExecutor,
        checkpoint_processor: CheckpointProcessor,
        knowledge_manager: KnowledgeManager,
        knowledge_loader: KnowledgeLoader,
        personality_manager: PersonalityManager,
        quality_evaluator: QualityEvaluator,
        model_selector: ModelSelector,
        tool_registry: ToolRegistry,
        llm_gateway: LLMGateway,
        max_plan_retries: int = 3,
        max_quality_retries: int = 2,
    ) -> None:

        self._analyzer = analyzer
        self._planner = planner
        self._pipeline_driver = pipeline_driver
        self._stage_executor = stage_executor
        self._checkpoint_processor = checkpoint_processor
        self._knowledge_manager = knowledge_manager
        self._knowledge_loader = knowledge_loader
        self._personality_manager = personality_manager
        self._quality_evaluator = quality_evaluator
        self._model_selector = model_selector
        self._tool_registry = tool_registry
        self._llm_gateway = llm_gateway

        self._max_make_plan_retries = max_plan_retries
        self._max_task_retries = max_quality_retries

        self._task: Task | None = None

        # Cross-thread control signals
        self._cancelled = threading.Event()
        # Optional callback to push progress messages to the user
        self._send_to_user: Callable[[ClientMessage], None] | None = None

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

    def run(self, task_description: str) -> TaskResult:
        self._cancelled.clear()

        # ── 1.1 分析Task特征 ──────────────────────────────────────────
        task = self._analyzer.analyze(
            task_description=task_description,
            llm_gateway=self._llm_gateway,
            knowledge_loader=self._knowledge_loader,
            personality_manager=self._personality_manager,
            tool_registry=self._tool_registry,
        )
        self._task = task

        # 1.1.4 发布"分析报告已出"事件
        self._pipeline_driver.publish_event(
            TaskAnalysisCompleted(task_id=task.id, content=task.intent)
        )
        # ── 1.2 根据Task特征匹配处理模型 ──────────────────────────────
        routing = self._model_selector.route(task=self._task, enable_fallback=True)
        provider_chain = [routing.primary] + routing.fallbacks

        # ── 1.3 制定并评审执行计划（含重试循环）──────────────────────
        plan = self._planner.make_plan(task, self._llm_gateway, self._quality_evaluator, self._pipeline_driver)
        if plan is None:
            event = TaskExecutionFailed(task_id=task.id, content="Failed to produce a valid plan")
            self._pipeline_driver.publish_event(event)
            return self._failed_result(task.id, "Failed to produce a valid plan after retries")

        # 1.3.2.1.1 发布"执行计划已确定"事件
        self._pipeline_driver.publish_event(
            ExecutionPlanFinalized(task_id=task.id, plan_id=plan.id, content="")
        )

        # ── 1.4 发布"Task已开始执行"事件 ─────────────────────────────
        self._pipeline_driver.publish_event(
            TaskExecutionStarted(task_id=task.id, content="")
        )

        # ── 1.5 按照计划执行 ──────────────────────────────────────────
        current_task_retries = 0
        while True:
            raw_result = self._stage_executor.execute(plan=plan, provider_chain=provider_chain)

            # 1.5.2 执行失败
            if raw_result is None:
                event = TaskExecutionFailed(task_id=task.id, content="Stage execution failed")
                self._pipeline_driver.publish_event(event)
                return self._failed_result(task.id, "Stage execution failed")

            # 1.5.1 执行成功 → 评审任务结果
            review = self._quality_evaluator.evaluate_task_result(
                task=task, result=raw_result, llmgateway=self._llm_gateway
            )

            if review.passed:
                # 1.5.1.1.1 异步提取任务经验和知识
                self._extract_knowledge_async(task_description, raw_result)
                # 1.5.1.1.2 从用户建议里总结用户偏好并落地
                self._extract_preferences_async(task_description)
                # 1.5.1.1.3 发布"Task执行结果信息"事件
                self._pipeline_driver.publish_event(
                    TaskResultProduced(task_id=task.id, content=raw_result)
                )
                return TaskResult(
                    task_id=task.id,
                    succeeded=True,
                    result=raw_result,
                    error_reason="",
                    delivered_at=datetime.now(timezone.utc),
                )

            # 1.5.1.2 评审不通过 → 清空上下文，结合评审意见重新制定计划
            current_task_retries += 1
            if current_task_retries > self._max_task_retries:
                event = TaskExecutionFailed(
                    task_id=task.id, content="Quality check failed after retries"
                )
                self._pipeline_driver.publish_event(event)
                return self._failed_result(task.id, "Quality check failed after retries")

            self._stage_executor.archive_current_stage_context()
            plan = self._planner.renew_plan(
                task=task, feedback=review.feedback, llm_api=self._llm_gateway
            )

    def continue_from_checkpoint(self, task_id: TaskId, cpt_id: CheckpointId) -> TaskResult:
        """Restore from the latest checkpoint and resume execution."""
        checkpoint = self._checkpoint_processor.restore_latest()
        if checkpoint is None:
            return self._failed_result(task_id, "No checkpoint found")

        self._stage_executor.replace_conversation_history(
            checkpoint.conversation_checkpoint
        )
        task_description = self._task.description if self._task else ""
        return self.run(task_description)

    # ------------------------------------------------------------------
    # Async side-effects
    # ------------------------------------------------------------------

    def _extract_knowledge_async(self, task_description: str, result: str) -> None:
        summary = f"Task: {task_description}\nResult: {result}"

        def _run() -> None:
            try:
                self._knowledge_manager.extract_and_save(summary, self._llm_gateway)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _extract_preferences_async(self, task_description: str) -> None:
        def _run() -> None:
            try:
                self._personality_manager.extract_and_save_user_preference(
                    task_description, self._llm_gateway
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _save_checkpoint_async(self, task_id: TaskId, stage_order: int) -> None:
        conversation = self._stage_executor.get_conversation_history()

        def _save() -> None:
            try:
                self._checkpoint_processor.save(task_id, stage_order, conversation)
            except Exception:
                pass

        threading.Thread(target=_save, daemon=True).start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_analysis_report(self, task: Task) -> str:
        lines = [
            f"Task analysis complete:",
            f"  type: {task.task_type}",
            f"  intent: {task.intent}",
            f"  complexity: {task.complexity.level}/5",
        ]
        if task.required_tools:
            lines.append(f"  tools: {', '.join(task.required_tools)}")
        if task.related_knowledge_entries:
            lines.append(f"  knowledge entries: {len(task.related_knowledge_entries)}")
        if task.related_user_preference_entries:
            lines.append(f"  preference entries: {len(task.related_user_preference_entries)}")
        return "\n".join(lines)

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
