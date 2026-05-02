from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.ids import PlanStepId, StageId, TaskId
from schemas.types import LLMMessage, ToolCall, ToolResult
from schemas.errors import AgentError, LLMError, TOOL_NOT_FOUND, TOOL_ARGUMENT_ERROR, build_error

from agent.models.reasoning.decision import NextDecisionType

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
    clarification_question: str = ""
    iteration_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def increment_iteration(self) -> None:
        self.iteration_count += 1

    def complete(self, result: str) -> None:
        self.status = StageStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, reason: str = "") -> None:
        self.status = StageStatus.FAILED
        self.result = reason
        self.completed_at = datetime.now(timezone.utc)

    def interrupt(self, guidance: str) -> None:
        self.status = StageStatus.INTERRUPTED
        self.interrupt_guidance = guidance
        self.completed_at = datetime.now(timezone.utc)

    def pause(self, question: str = "") -> None:
        self.status = StageStatus.PAUSED
        self.clarification_question = question


class StageExecutor:
    """Aggregate root that executes a single Stage (one PlanStep).

    Drives the ReAct reasoning loop: reason → tool call → inject result → repeat.
    Delegates single-step LLM inference to ReasoningManager and tool dispatch to
    ToolRegistry. Context management (including trimming) is handled by ContextManager.
    """

    def __init__(
        self,
        reasoning_manager: ReasoningManager,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        quality_evaluator: QualityEvaluator,
        knowledge_loader: KnowledgeLoader,
        max_iterations: int = 60,
        forbidden_tools: list[str] | None = None,
    ) -> None:
        self._reasoning_manager = reasoning_manager
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._quality_evaluator = quality_evaluator
        self._knowledge_loader = knowledge_loader
        self._max_iterations = max_iterations
        self._forbidden_tools: frozenset[str] = (
            frozenset(forbidden_tools) if forbidden_tools else frozenset()
        )
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
        resume_existing_context: bool = False,
        provider_name: str | None = None,
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
        last_answer = ""
        selected_tool_names: list[str] | None = None
        while stage.iteration_count < self._max_iterations:
            # Check for interrupt / pause
            if self._interrupted.is_set():
                stage.interrupt(self._interrupt_guidance)
                break

            if self._paused.is_set():
                stage.pause()
                break

            try:
                decision = self._reasoning_manager.reason_once(
                    self._context_manager,
                    self._tool_registry,
                    selected_tool_names,
                    provider_name,
                )
            except LLMError:
                # Let Pipeline classify provider fallback vs pause/resume.
                raise
            except AgentError as exc:
                stage.fail(f"Agent error: {exc.message}")
                break

            stage.increment_iteration()

            if decision.decision_type == NextDecisionType.FINAL_ANSWER:
                last_answer = decision.answer
                stage.complete(last_answer)
                break

            if decision.decision_type == NextDecisionType.CONTINUE:
                # Truncated or plain reasoning — inject and continue
                content = decision.message or (
                    decision.assistant_message.content
                    if decision.assistant_message
                    else ""
                )
                self._context_manager.add_message("assistant", content)
                selected_tool_names = None
                continue

            if decision.decision_type == NextDecisionType.TOOL_CALL:
                if decision.assistant_message:
                    self._context_manager.add_message(
                        decision.assistant_message.role,
                        decision.assistant_message.content,
                        decision.assistant_message.metadata,
                    )
                self._dispatch_tool_calls(decision.tool_calls)
                # Next iteration: only expose the tools the LLM just selected
                selected_tool_names = [tc.name for tc in decision.tool_calls]
                continue

            if decision.decision_type == NextDecisionType.CLARIFICATION_NEEDED:
                stage.pause(decision.message)
                if decision.message:
                    self._context_manager.add_message("assistant", decision.message)
                break

        else:
            # Max iterations reached
            stage.fail(last_answer)

        return stage

    # ------------------------------------------------------------------
    # Interrupt / pause / resume
    # ------------------------------------------------------------------

    def interrupt(self, guidance: str) -> None:
        """Called by Pipeline to interrupt the current Stage."""
        self._interrupt_guidance = guidance
        self._interrupted.set()

    def pause(self, reason: str) -> None:
        """Called by Pipeline to pause the current Stage."""
        self._pause_reason = reason
        self._paused.set()

    def resume(self) -> None:
        """Called by Pipeline to resume a paused Stage."""
        self._paused.clear()

    def get_current_stage(self) -> Stage | None:
        return self._current_stage

    def reset_for_next_stage(self) -> None:
        """Clear context for the next Stage execution."""
        self._context_manager.reset()
        self._current_stage = None
        self._interrupted.clear()
        self._paused.clear()

    def archive_current_stage_context(self) -> None:
        """Preserve the just-completed Stage context for later stages/checkpoints."""
        self._context_manager.archive_current_task()

    def append_user_clarification(self, clarification: str) -> None:
        self._context_manager.add_message("user", f"Clarification: {clarification}")

    def set_llm_gateway(self, llm_gateway: object) -> None:
        self._reasoning_manager.set_llm_gateway(llm_gateway)  # type: ignore[arg-type]

    def replace_conversation_history(self, messages: list[LLMMessage]) -> None:
        self._context_manager.replace_conversation_history(messages)

    def get_conversation_history(self) -> list[LLMMessage]:
        return self._context_manager.get_conversation_history()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_knowledge(self, query: str) -> None:
        entries = self._knowledge_loader.load(query)
        if entries:
            snippets = "\n".join(f"- {e.content}" for e in entries)
            variables = self._context_manager.get_variables()
            variables["reusable_knowledge"] = snippets
            self._context_manager.set_variables(variables)

    def _dispatch_tool_calls(
        self,
        tool_calls: list[ToolCall],
    ) -> None:
        for tool_call in tool_calls:
            rejection = self._check_tool_call(tool_call)
            if rejection is not None:
                observation = self._reasoning_manager.format_tool_observation(
                    tool_call=tool_call,
                    result=rejection,
                )
                self._context_manager.add_message(
                    observation.role,
                    observation.content,
                    observation.metadata,
                )
                continue

            result: ToolResult = self._tool_registry.execute(tool_call)
            observation = self._reasoning_manager.format_tool_observation(
                tool_call=tool_call,
                result=self._tool_result_for_observation(result),
            )
            self._context_manager.add_message(
                observation.role,
                observation.content,
                observation.metadata,
            )

    def _check_tool_call(self, tool_call: ToolCall) -> ToolResult | None:
        """Return a failed ToolResult if the call is not permitted or malformed, else None."""
        if not self._tool_registry.has_tool(tool_call.name):
            available = ", ".join(s["name"] for s in self._tool_registry.get_tool_schemas())
            return ToolResult(
                output="",
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                success=False,
                error=build_error(
                    TOOL_NOT_FOUND,
                    f"Tool '{tool_call.name}' does not exist. Available tools: {available}.",
                ),
            )

        if self._forbidden_tools and tool_call.name in self._forbidden_tools:
            return ToolResult(
                output="",
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                success=False,
                error=build_error(
                    TOOL_NOT_FOUND,
                    f"Tool '{tool_call.name}' is forbidden and cannot be called.",
                ),
            )

        missing = self._tool_registry.validate_arguments(tool_call)
        if missing:
            return ToolResult(
                output="",
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                success=False,
                error=build_error(
                    TOOL_ARGUMENT_ERROR,
                    f"Tool '{tool_call.name}' is missing required arguments: {', '.join(missing)}.",
                ),
            )

        return None

    @staticmethod
    def _tool_result_for_observation(result: ToolResult) -> ToolResult:
        if result.success or result.output or result.error is None:
            return result
        return ToolResult(
            output=f"Tool call failed: [{result.error.code}] {result.error.message}",
            llm_raw_tool_call_id=result.llm_raw_tool_call_id,
            success=False,
            error=result.error,
        )
