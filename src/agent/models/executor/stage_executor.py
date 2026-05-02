from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.ids import PlanStepId, StageId, TaskId
from schemas.types import LLMMessage, ToolCall, ToolResult
from schemas.errors import AgentError, LLMError, TOOL_NOT_FOUND, TOOL_ARGUMENT_ERROR, build_error

from schemas.task import NextDecisionType, StageStatus

if TYPE_CHECKING:
    from agent.models.context.manager import ContextManager
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.reasoning.reasoning_manager import ReasoningManager
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from tools.tool_registry import ToolRegistry


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
    def execute(
        self,
        task: Task,
        plan: Plan,
        provider_routing: list[str],
        async_queue: queue
    ) -> str | None:
        current_stage = construct_stage() 
        while current_stage is not None:
            loop_user_cancel_information
            try:
                #尝试切回最好的模型
                stage = self._stage_executor.execute_stage(
                    task_id=task_id,
                    plan_step_id=step.id,
                    plan_step_goal=step.goal,
                    plan_step_description=step.description,
                    resume_existing_context=resume_existing_context,
                    provider_name=provider_chain[provider_index],
                )
                current_stage = next_stage
                # if need save checkpoint
                self._stage_executor.archive_current_stage_context()
                self._save_checkpoint_async(task_id, step_index)

            except LLMError as exc:
                if exc.category in (
                    ErrorCategory.AUTH,
                    ErrorCategory.CONFIG,
                    ErrorCategory.RESPONSE,
                ):
                    #切模型
                    next_index = self._next_provider_index(provider_chain, provider_index)
                    provider_index = next_index
                    #需要知道stage涉及的context上下文，reset重新retry当前stage
                    continue
                else:
                    #TODO
            except AgentError as exc:
                #if 需要revise stage plan:
                #    revise_stage_plan()
                    #reset stage context
                    #current_stage = construct_stage() 
                    continue
                #else
                return None
        return last_result

    def execute_stage(
        self,
        task_id: TaskId,
        plan_step_id: PlanStepId,
        plan_step_goal: str,
        plan_step_description: str,
        resume_existing_context: bool = False,
        provider_name: str | None = None,
        stage
    ) -> Stage:
        while stage.iteration_count < self._max_iterations:
            #loop_user_guidance指引,也许拿的到
            #context manager + user guide
            # return need revise plan
            try:
                context_window = (context_manager.prepare_context(provider_name)
                    if provider_name
                    else context_manager.get_context_window()
                )
                decision = self._reasoning_manager.reason_once(
                    context_window,
                    self._tool_registry.get_tool_schemas,
                )
            except AgentError as exc:#hard error
                stage.fail(f"Agent error: {exc.message}")
                break

            if decision.decision_type == NextDecisionType.FINAL_ANSWER:
                last_answer = decision.answer
                #评估这个stage
                #if passed -> 用上下文联通？
                #else return revise plan, 只有评估不过可以revise

            if decision.decision_type == NextDecisionType.CONTINUE:
                # Truncated or plain reasoning — inject and continue
                content = decision.message or (
                    decision.assistant_message.content
                    if decision.assistant_message
                    else ""
                )
                self._context_manager.add_message("assistant", content)
                stage.iteration_count += 1 
            if decision.decision_type == NextDecisionType.TOOL_CALL:
                if decision.assistant_message:
                    self._context_manager.add_message(
                        decision.assistant_message.role,
                        decision.assistant_message.content,
                        decision.assistant_message.metadata,
                    )
                self._dispatch_tool_calls(decision.tool_calls)
                # Next iteration: only expose the tools the LLM just selected
                stage.iteration_count += 1 
            if decision.decision_type == NextDecisionType.CLARIFICATION_NEEDED:
                #阻塞等待澄清
                if decision.message:
                    self._context_manager.add_message("assistant", decision.message)

            if decision.decision_type == NextDecisionType.PAUSE:
                #阻塞等待继续

        #尽最大努力挂了，然后？

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
            #如果结果失败并且是搜索，考虑本地内容
            
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
