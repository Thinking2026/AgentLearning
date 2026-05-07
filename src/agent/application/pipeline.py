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
)
from agent.factory.agent_factory import AgentFactory
from schemas.ids import CheckpointId, TaskId
from schemas.task import Plan, Task, TaskResult
from schemas.types import ClientMessage

if TYPE_CHECKING:
    from agent.models.analysis.analyzer import Analyzer
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from agent.models.executor.stage_executor import StageExecutor
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.knowledge.knowledge_manager import KnowledgeManager
    from agent.models.model_routing.provider_router import ModelSelector
    from agent.models.personality.user_preference import PersonalityManager
    from agent.models.plan.planner import Planner
    from infra.observability.tracing import Span, Tracer
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
        agent_factory: AgentFactory,
        pipeline_driver: PipelineDriver,
        max_plan_retries: int = 3,
        max_quality_retries: int = 2,
    ) -> None:
        self._agent_factory = agent_factory
        self._analyzer = self._agent_factory.build_analyzer()
        self._planner = self._agent_factory.build_planner()
        self._pipeline_driver = pipeline_driver
        self._stage_executor = self._agent_factory.build_stage_executor()
        self._knowledge_manager = self._agent_factory.build_knowledge_manager()
        self._knowledge_loader = self._agent_factory.build_knowledge_loader()
        self._personality_manager = self._agent_factory.build_personality_manager()
        self._quality_evaluator = self._agent_factory.build_quality_evaluator()
        self._model_selector = self._agent_factory.build_model_selector()
        self._tool_registry = self._agent_factory.build_tool_registry()
        self._llm_gateway = self._agent_factory.build_llm_gateway()
        self._tracer = self._agent_factory.build_tracer()

        self._max_make_plan_retries = max_plan_retries
        self._max_task_retries = max_quality_retries

        self._task: Task | None = None
        self._session_span: Span | None = None

        # Cross-thread control signals
        self._cancelled = threading.Event()
    # ------------------------------------------------------------------
    # Public control API (thread-safe, called from PipelineThread)
    # ------------------------------------------------------------------

    @property
    def stage_executor(self) -> StageExecutor:
        return self._stage_executor

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, task_description: str) -> TaskResult:
        self._cancelled.clear()
        self._start_session_trace(task_description)

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
            result = self._failed_result(task.id, "Failed to produce a valid plan after retries")
            self._finish_session_trace(error=result.error_reason or None)
            return result

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
                result = self._failed_result(task.id, "Stage execution failed")
                self._finish_session_trace(error=result.error_reason or None)
                return result

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
                result = TaskResult(
                    task_id=task.id,
                    succeeded=True,
                    result=raw_result,
                    error_reason="",
                    delivered_at=datetime.now(timezone.utc),
                )
                self._finish_session_trace()
                return result

            # 1.5.1.2 评审不通过 → 清空上下文，结合评审意见重新制定计划
            current_task_retries += 1
            if current_task_retries > self._max_task_retries:
                event = TaskExecutionFailed(
                    task_id=task.id, content="Quality check failed after retries"
                )
                self._pipeline_driver.publish_event(event)
                result = self._failed_result(task.id, "Quality check failed after retries")
                self._finish_session_trace(error=result.error_reason or None)
                return result

            self._stage_executor.archive_current_stage_context()
            plan = self._planner.renew_plan(
                task=task, feedback=review.feedback, llm_api=self._llm_gateway
            )

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------

    def _start_session_trace(self, task_description: str) -> None:
        if self._tracer is None or self._session_span is not None:
            return
        self._session_span = self._tracer.start_trace(
            "session",
            attributes={"task": task_description},
        )

    def _finish_session_trace(self, error: str | None = None) -> None:
        if self._session_span is None:
            return
        status = "error" if error else "ok"
        self._session_span.finish(status=status, error=error)
        self._session_span = None

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
        self._llm_gateway.switch_provider(provider_name)

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
