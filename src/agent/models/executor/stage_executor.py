from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from schemas.domain import AggregateRoot
from schemas.ids import PlanStepId, StageId, TaskId
from schemas.types import LLMMessage, ToolCall, ToolResult, UIMessage
from schemas.errors import AgentError, LLMError, ErrorCategory

from agent.events import (
    TaskExecutionStarted,
    TaskStepInterrupted,
    TaskPaused,
    TaskResumed,
    StepResultProduced,
    ToolCallRequested,
    ResultInjected,
    ClarificationRequested,
)
from agent.models.reasoning.decision import NextDecision, NextDecisionType

if TYPE_CHECKING:
    from agent.models.context.manager import ContextManager
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.reasoning.reasoning_manager import ReasoningManager
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from tools.tool_registry import ToolRegistry


class StageStatus(str, Enum):
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    INTERRUPTED  = "INTERRUPTED"
    PAUSED       = "PAUSED"
    FAILED       = "FAILED"


@dataclass
class Stage:
    id: StageId
    task_id: TaskId
    plan_step_id: PlanStepId
    plan_step_goal: str
    plan_step_description: str
    status: StageStatus = StageStatus.RUNNING
    result: str = ""
    interrupt_guidance: str = ""
    iteration_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class StageExecutor(AggregateRoot):
    """Aggregate root that executes a single Stage (one PlanStep).

    Drives the ReAct reasoning loop: reason → tool call → inject result → repeat.
    Delegates single-step LLM inference to ReasoningManager and tool dispatch to
    ToolRegistry. Context management is handled by ContextManager.
    """

    def __init__(
        self,
        reasoning_manager: ReasoningManager,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        quality_evaluator: QualityEvaluator,
        knowledge_loader: KnowledgeLoader,
        max_iterations: int = 60,
    ) -> None:
        super().__init__()
        self._reasoning_manager = reasoning_manager
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._quality_evaluator = quality_evaluator
        self._knowledge_loader = knowledge_loader
        self._max_iterations = max_iterations
        self._current_stage: Stage | None = None
        self._interrupted = threading.Event()
        self._paused = threading.Event()
        self._pause_reason: str = ""
        self._interrupt_guidance: str = ""

    # ------------------------------------------------------------------
    # Main execution entry point
    # ------------------------------------------------------------------

    def execute_stage(
        self,
        task_id: TaskId,
        plan_step_id: PlanStepId,
        plan_step_goal: str,
        plan_step_description: str,
        on_message: Callable[[UIMessage], None] | None = None,
    ) -> Stage:
        """Execute one Stage end-to-end.

        1. Create Stage instance, load reusable knowledge into context.
        2. Loop: reason_once → handle decision → inject result.
        3. Update Stage status and result.
        """
        self._interrupted.clear()
        self._paused.clear()

        stage = Stage(
            id=StageId(f"stage_{uuid4().hex}"),
            task_id=task_id,
            plan_step_id=plan_step_id,
            plan_step_goal=plan_step_goal,
            plan_step_description=plan_step_description,
        )
        self._current_stage = stage

        self._record(TaskExecutionStarted(
            event_type="", aggregate_id=str(task_id),
            task_id=task_id, stage_id=stage.id, step_id=plan_step_id,
        ))

        # Load reusable knowledge and inject into context
        self._load_knowledge(plan_step_goal)

        # Seed the context with the step goal
        self._context_manager.append_conversation_message(
            LLMMessage(role="user", content=plan_step_goal)
        )

        last_answer = ""
        while stage.iteration_count < self._max_iterations:
            # Check for interrupt / pause
            if self._interrupted.is_set():
                stage.status = StageStatus.INTERRUPTED
                stage.interrupt_guidance = self._interrupt_guidance
                break

            if self._paused.is_set():
                stage.status = StageStatus.PAUSED
                break

            try:
                decision = self._reasoning_manager.reason_once(
                    self._context_manager,
                    self._tool_registry,
                )
            except LLMError as exc:
                if exc.category in (ErrorCategory.AUTH, ErrorCategory.CONFIG):
                    stage.status = StageStatus.FAILED
                    stage.result = f"LLM error: {exc.message}"
                    break
                # For transient/rate-limit errors, propagate so Pipeline can handle
                raise
            except AgentError as exc:
                stage.status = StageStatus.FAILED
                stage.result = f"Agent error: {exc.message}"
                break

            stage.iteration_count += 1

            if decision.decision_type == NextDecisionType.FINAL_ANSWER:
                last_answer = decision.answer
                if on_message:
                    on_message(UIMessage(
                        role="assistant",
                        content=decision.answer,
                        metadata={"task_completed": True},
                    ))
                stage.status = StageStatus.COMPLETED
                stage.result = last_answer
                stage.completed_at = datetime.now(timezone.utc)
                self._record(StepResultProduced(
                    event_type="", aggregate_id=str(task_id),
                    task_id=task_id, stage_id=stage.id,
                    step_id=plan_step_id, result=last_answer,
                ))
                break

            if decision.decision_type == NextDecisionType.CONTINUE:
                # Truncated or plain reasoning — inject and continue
                content = decision.message or (decision.assistant_message.content if decision.assistant_message else "")
                if content and on_message:
                    on_message(UIMessage(role="assistant", content=content, metadata={"source": "llm"}))
                self._context_manager.append_conversation_message(
                    LLMMessage(role="assistant", content=content)
                )
                self._record(ResultInjected(
                    event_type="", aggregate_id=str(task_id),
                    task_id=task_id, stage_id=stage.id,
                ))
                continue

            if decision.decision_type == NextDecisionType.TOOL_CALL:
                # Stream assistant thinking content if any
                if decision.assistant_message and decision.assistant_message.content.strip() and on_message:
                    on_message(UIMessage(
                        role="assistant",
                        content=decision.assistant_message.content,
                        metadata={"source": "llm"},
                    ))
                if decision.assistant_message:
                    self._context_manager.append_conversation_message(decision.assistant_message)
                self._dispatch_tool_calls(task_id, stage, decision.tool_calls, on_message)
                continue

            if decision.decision_type == NextDecisionType.CLARIFICATION_NEEDED:
                stage.status = StageStatus.PAUSED
                self._record(ClarificationRequested(
                    event_type="", aggregate_id=str(task_id),
                    task_id=task_id, question=decision.message,
                ))
                break

        else:
            # Max iterations reached
            stage.status = StageStatus.FAILED
            stage.result = last_answer

        return stage

    # ------------------------------------------------------------------
    # Interrupt / pause / resume
    # ------------------------------------------------------------------

    def interrupt(self, guidance: str) -> None:
        """Called by Pipeline to interrupt the current Stage."""
        self._interrupt_guidance = guidance
        self._interrupted.set()
        if self._current_stage:
            self._record(TaskStepInterrupted(
                event_type="", aggregate_id=str(self._current_stage.task_id),
                task_id=self._current_stage.task_id,
                stage_id=self._current_stage.id,
                guidance=guidance,
            ))

    def pause(self, reason: str) -> None:
        """Called by Pipeline to pause the current Stage."""
        self._pause_reason = reason
        self._paused.set()
        if self._current_stage:
            self._record(TaskPaused(
                event_type="", aggregate_id=str(self._current_stage.task_id),
                task_id=self._current_stage.task_id,
                reason=reason,
            ))

    def resume(self) -> None:
        """Called by Pipeline to resume a paused Stage."""
        self._paused.clear()
        if self._current_stage:
            self._record(TaskResumed(
                event_type="", aggregate_id=str(self._current_stage.task_id),
                task_id=self._current_stage.task_id,
            ))

    def get_current_stage(self) -> Stage | None:
        return self._current_stage

    def reset_for_next_stage(self) -> None:
        """Clear context for the next Stage execution."""
        self._context_manager.clear_current_task()
        self._current_stage = None
        self._interrupted.clear()
        self._paused.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_knowledge(self, query: str) -> None:
        entries = self._knowledge_loader.load(query)
        if entries:
            snippets = "\n".join(f"- {e.content}" for e in entries)
            self._context_manager.append_system_prompt(
                f"\n\nRelevant prior knowledge:\n{snippets}"
            )

    def _dispatch_tool_calls(
        self,
        task_id: TaskId,
        stage: Stage,
        tool_calls: list[ToolCall],
        on_message: Callable[[UIMessage], None] | None,
    ) -> None:
        for tool_call in tool_calls:
            self._record(ToolCallRequested(
                event_type="", aggregate_id=str(task_id),
                task_id=task_id, tool_name=tool_call.name,
            ))

            result: ToolResult = self._tool_registry.execute(
                tool_call.name,
                tool_call.arguments,
                tool_call.llm_raw_tool_call_id,
            )

            if on_message:
                on_message(UIMessage(
                    role="assistant",
                    content=f"[tool:{tool_call.name}] {result.output}",
                    metadata={
                        "source": "tool",
                        "tool_name": tool_call.name,
                        "tool_arguments": tool_call.arguments,
                        "tool_result": result.output,
                        "tool_success": result.success,
                    },
                ))

            # Format and inject tool result into context
            observation = self._reasoning_manager._strategy.format_tool_observation(
                tool_call=tool_call,
                result=result,
            )
            self._context_manager.append_conversation_message(observation)
            self._record(ResultInjected(
                event_type="", aggregate_id=str(task_id),
                task_id=task_id, stage_id=stage.id,
            ))
