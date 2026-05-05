from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from agent.events.events import (
    LLMResponseGenerated,
    TaskCancelled,
    TaskPaused,
    ToolCallResultProduced,
    ToolCallStarted,
    UserClarificationRequested,
)
from schemas.ids import PlanStepId, StageId, TaskId
from schemas.types import LLMMessage, ToolCall, ToolResult
from schemas.errors import (
    AgentError,
    LLMError,
    ErrorCategory,
    TOOL_NOT_FOUND,
    TOOL_ARGUMENT_ERROR,
    build_error,
)
from schemas.task import NextDecisionType, Plan, PlanStep, StageStatus, PlanUpdateTrigger, Task

if TYPE_CHECKING:
    from agent.application.driver import PipelineDriver
    from agent.models.context.manager import ContextManager
    from agent.models.knowledge.knowledge_loader import KnowledgeLoader
    from agent.models.reasoning.reasoning_manager import ReasoningManager
    from agent.models.evaluate.quality_evaluator import QualityEvaluator
    from agent.models.plan.planner import Planner
    from tools.tool_registry import ToolRegistry


@dataclass
class Stage:
    id: StageId
    task_id: TaskId
    plan_step_id: PlanStepId
    plan_step_goal: str
    plan_step_description: str
    plan_step_key_results: list[str] = field(default_factory=list)
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
    """Drives the Stage Level loop and the Stage internal reasoning loop.

    Stage Level (execute): iterates over plan steps, handles eval/retry/model-switch.
    Reasoning loop (_execute_stage): ReAct loop — reason → tool → inject → repeat.

    Interrupt/pause/resume are signalled via threading.Event so Pipeline can call
    interrupt() / pause() / resume() from a different thread while the loop runs.
    """

    def __init__(
        self,
        driver: PipelineDriver,
        reasoning_manager: ReasoningManager,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        quality_evaluator: QualityEvaluator,
        knowledge_loader: KnowledgeLoader,
        max_iterations: int = 60,
        max_stage_eval_retries: int = 2,
        forbidden_tools: list[str] | None = None,
    ) -> None:
        self._driver = driver
        self._reasoning_manager = reasoning_manager
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._quality_evaluator = quality_evaluator
        self._knowledge_loader = knowledge_loader
        self._max_iterations = max_iterations
        self._max_stage_eval_retries = max_stage_eval_retries
        self._forbidden_tools: frozenset[str] = (
            frozenset(forbidden_tools) if forbidden_tools else frozenset()
        )
        self._current_stage: Stage | None = None
        self._current_stage_index: int = 0

        # Wire tool schemas into context manager once at construction time
        self._context_manager.set_tool_schemas(tool_registry.get_tool_schemas())

        # Signalled by Pipeline from its thread
        self._interrupted = threading.Event()
        self._paused = threading.Event()
        self._cancelled = threading.Event()
        self._clarification_ready = threading.Event()

        self._interrupt_guidance: str = ""
        self._pause_reason: str = ""
        self._clarification_text: str = ""

    # ------------------------------------------------------------------
    # Public control API (called by Pipeline from its thread)
    # ------------------------------------------------------------------

    def interrupt(self, guidance: str) -> None:
        self._interrupt_guidance = guidance
        self._interrupted.set()

    def pause(self, reason: str) -> None:
        self._pause_reason = reason
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def cancel(self) -> None:
        self._cancelled.set()

    def provide_clarification(self, text: str) -> None:
        self._clarification_text = text
        self._clarification_ready.set()

    # ------------------------------------------------------------------
    # Stage Level loop
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: Plan,
        provider_chain: list[str],
    ) -> str | None:
        """Iterate over all plan steps and return the final answer, or None on failure."""
        provider_index = 0
        step_index = 0
        last_result = ""

        while step_index < planner.total_steps():
            if self._cancelled.is_set():
                return None

            step = planner.get_step_by_order(step_index)
            if step is None:
                break

            stage_eval_retries = 0

            while True:
                if self._cancelled.is_set():
                    return None

                provider_name = (
                    provider_chain[provider_index]
                    if provider_index < len(provider_chain)
                    else provider_chain[-1]
                )

                stage = Stage(
                    id=StageId(str(uuid4())),
                    task_id=task.id,
                    plan_step_id=step.id,
                    plan_step_goal=step.goal,
                    plan_step_description=step.description,
                    plan_step_key_results=list(step.key_results),
                )
                self._current_stage = stage
                self._interrupted.clear()

                try:
                    self._execute_stage(stage, provider_name)
                except LLMError as exc:
                    # Provider-level failure: try next provider
                    if exc.category in (
                        ErrorCategory.AUTH,
                        ErrorCategory.CONFIG,
                        ErrorCategory.RESPONSE,
                    ):
                        next_idx = provider_index + 1
                        if next_idx >= len(provider_chain):
                            return None
                        provider_index = next_idx
                        self._context_manager.reset()
                        continue
                    return None
                except AgentError:
                    return None

                if stage.status == StageStatus.COMPLETED:
                    eval_record = self._quality_evaluator.evaluate_step_result(step, stage.result)
                    if eval_record.passed:
                        last_result = stage.result
                        self.archive_current_stage_context()
                        self._current_stage_index += 1
                        step_index += 1
                        break
                    else:
                        if stage_eval_retries >= self._max_stage_eval_retries:
                            return None
                        stage_eval_retries += 1
                        planner.revise(
                            step.id,
                            PlanUpdateTrigger.STAGE_EVAL_FAILED,
                            eval_record.feedback,
                        )
                        step = planner.get_step(step.id) or step
                        self._context_manager.reset()

                elif stage.status == StageStatus.INTERRUPTED:
                    # User guidance: revise this step and retry
                    planner.revise(
                        step.id,
                        PlanUpdateTrigger.USER_GUIDANCE,
                        stage.interrupt_guidance,
                    )
                    step = planner.get_step(step.id) or step
                    self._context_manager.reset()
                    stage_eval_retries = 0

                elif stage.status == StageStatus.PAUSED:
                    # B-class error or clarification wait: Pipeline already handles
                    # resume signalling; just reset and retry the stage
                    self._context_manager.reset()

                elif stage.status == StageStatus.FAILED:
                    return None

        return last_result or None

    # ------------------------------------------------------------------
    # Stage internal reasoning loop
    # ------------------------------------------------------------------

    def _execute_stage(self, stage: Stage, provider_name: str) -> None:
        """ReAct reasoning loop for a single stage.

        Flow per iteration:
          3.   Poll async user commands (cancel / guidance).
          1.   get_context_window — truncation handled inside ContextManager.
          2.   Call LLM → Decision.
          2.0  Publish LLMResponseGenerated event.
          2.1  FINAL_ANSWER  → complete stage, return.
          2.2  CONTINUE      → inject assistant message, loop.
          2.3  TOOL_CALL     → dispatch tools (with events), loop.
          2.4  CLARIFICATION → publish event, block, inject reply, loop.
          2.5  PAUSED        → publish event, block, resume, loop.
        """
        self._context_manager.begin_stage(self._current_stage_index)

        stage_prompt_lines = [
            f"## Stage Goal: {stage.plan_step_goal}",
            "",
            f"**Description:** {stage.plan_step_description}",
        ]
        if stage.plan_step_key_results:
            stage_prompt_lines.append("")
            stage_prompt_lines.append("**Key Results Expected:**")
            for kr in stage.plan_step_key_results:
                stage_prompt_lines.append(f"- {kr}")
        stage_prompt_lines.append("")
        stage_prompt_lines.append(
            "Please complete this stage according to the description and key results above."
        )
        self._context_manager.add_message("user", "\n".join(stage_prompt_lines))

        while stage.iteration_count < self._max_iterations:

            # ── 3. Poll async user commands ────────────────────────────────
            user_cmd = self._driver.loop_user_messages()
            if user_cmd is not None:
                if user_cmd.type == "CANCEL":
                    # 3.1 cancel
                    self._cancelled.set()
                    self._driver.publish_event(
                        TaskCancelled(task_id=stage.task_id, content="Task cancelled by user.")
                    )
                    stage.fail("Cancelled by user.")
                    return
                if user_cmd.type == "GUIDANCE":
                    # 3.2 user correction — signal outer loop to replan this step
                    stage.interrupt(user_cmd.content or "")
                    return

            if self._cancelled.is_set():
                stage.fail("Cancelled.")
                return

            # ── 1. Get context window (truncation handled inside) ──────────
            try:
                unified_llm_request = self._context_manager.get_context_window(provider_name)
                # ── 2. Call LLM ────────────────────────────────────────────
                decision = self._reasoning_manager.reason_once(unified_llm_request)
            except LLMError:
                raise
            except AgentError as exc:
                stage.fail(f"Agent error: {exc.message}")
                return

            # 2.0 publish "LLM reply generated" event
            self._driver.publish_event(
                LLMResponseGenerated(
                    task_id=stage.task_id,
                    order=str(stage.iteration_count),
                    content=decision.message or (
                        decision.assistant_message.content
                        if decision.assistant_message else ""
                    ),
                )
            )

            # ── 2.1 Final answer ───────────────────────────────────────────
            if decision.decision_type == NextDecisionType.FINAL_ANSWER:
                stage.increment_iteration()
                stage.complete(decision.answer)
                self._context_manager.end_stage(self._current_stage_index, success=True)
                return

            # ── 2.2 Continue reasoning ─────────────────────────────────────
            if decision.decision_type == NextDecisionType.CONTINUE:
                content = decision.message or (
                    decision.assistant_message.content if decision.assistant_message else ""
                )
                self._context_manager.add_message("assistant", content)
                stage.increment_iteration()
                continue

            # ── 2.3 Tool call ──────────────────────────────────────────────
            if decision.decision_type == NextDecisionType.TOOL_CALL:
                if decision.assistant_message:
                    self._context_manager.add_message(
                        decision.assistant_message.role,
                        decision.assistant_message.content,
                        decision.assistant_message.metadata,
                    )
                self._dispatch_tool_calls(stage, decision.tool_calls)
                stage.increment_iteration()
                continue

            # ── 2.4 Clarification needed ───────────────────────────────────
            if decision.decision_type == NextDecisionType.CLARIFICATION_NEEDED:
                question = decision.message or "Please provide clarification."
                if decision.assistant_message:
                    self._context_manager.add_message(
                        "assistant", decision.assistant_message.content
                    )
                else:
                    self._context_manager.add_message("assistant", question)

                self._driver.publish_event(
                    UserClarificationRequested(
                        task_id=stage.task_id,
                        order=str(stage.iteration_count),
                        question=question,
                        content=question,
                    )
                )

                # block until clarification arrives
                self._clarification_ready.clear()
                self._clarification_ready.wait()
                self._clarification_ready.clear()

                if self._cancelled.is_set():
                    stage.fail("Cancelled while waiting for clarification.")
                    return

                # 2.4.1 inject clarification and loop back
                self._context_manager.add_message(
                    "user", f"Clarification: {self._clarification_text}"
                )
                stage.increment_iteration()
                continue

            # ── 2.5 Paused ────────────────────────────────────────────────
            if decision.decision_type == NextDecisionType.PAUSED:
                reason = decision.message or "Task paused."
                if decision.assistant_message:
                    self._context_manager.add_message(
                        "assistant", decision.assistant_message.content
                    )

                self._driver.publish_event(
                    TaskPaused(task_id=stage.task_id, reason=reason, content=reason)
                )
                stage.pause(reason)

                # block until resumed
                self._paused.wait()
                self._paused.clear()

                if self._cancelled.is_set():
                    stage.fail("Cancelled while paused.")
                    return

                # 2.5.1 resume — loop back
                stage.status = StageStatus.RUNNING
                stage.increment_iteration()
                continue

        stage.fail(f"Max iterations ({self._max_iterations}) exceeded")
        self._context_manager.end_stage(self._current_stage_index, success=False)

    # ------------------------------------------------------------------
    # Public helpers used by Pipeline
    # ------------------------------------------------------------------

    def get_current_stage(self) -> Stage | None:
        return self._current_stage

    def archive_current_stage_context(self) -> None:
        """Preserve the completed stage context (no-op: messages stay for next stage)."""
        pass

    def reset_for_next_stage(self) -> None:
        self._context_manager.reset()
        self._current_stage = None
        self._interrupted.clear()
        self._paused.clear()

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

    def _dispatch_tool_calls(self, stage: Stage, tool_calls: list[ToolCall]) -> None:
        for tool_call in tool_calls:
            # 2.3.0 publish "tool call started" event
            self._driver.publish_event(
                ToolCallStarted(
                    task_id=stage.task_id,
                    order=str(stage.iteration_count),
                    tool_name=tool_call.name,
                    arguments=dict(tool_call.arguments),
                    content=f"Calling tool: {tool_call.name}",
                )
            )

            # 2.3.1 pre-flight check
            rejection = self._check_tool_call(tool_call)
            if rejection is not None:
                # 2.3.1.2 check failed — inject system message so LLM can switch tools
                observation = self._reasoning_manager.format_tool_observation(
                    tool_call=tool_call,
                    result=rejection,
                )
                self._context_manager.add_message(
                    observation.role,
                    observation.content,
                    observation.metadata,
                )
                self._driver.publish_event(
                    ToolCallResultProduced(
                        task_id=stage.task_id,
                        order=str(stage.iteration_count),
                        tool_name=tool_call.name,
                        content=f"Tool pre-check failed: {tool_call.name}",
                    )
                )
                continue

            # 2.3.1.1 execute tool
            result: ToolResult = self._tool_registry.execute(tool_call)

            if not result.success and tool_call.name == "search":
                # 2.3.1.1.2.1 search fallback via knowledge base
                fallback = self._knowledge_search_fallback(tool_call)
                if fallback is not None:
                    result = fallback

            # 2.3.1.1.1 / 2.3.1.1.2.2 inject result and publish event
            observation = self._reasoning_manager.format_tool_observation(
                tool_call=tool_call,
                result=self._tool_result_for_observation(result),
            )
            self._context_manager.add_message(
                observation.role,
                observation.content,
                observation.metadata,
            )
            self._driver.publish_event(
                ToolCallResultProduced(
                    task_id=stage.task_id,
                    order=str(stage.iteration_count),
                    tool_name=tool_call.name,
                    content=f"Tool result: {tool_call.name} {'succeeded' if result.success else 'failed'}",
                )
            )

    def _check_tool_call(self, tool_call: ToolCall) -> ToolResult | None:
        if not self._tool_registry.has_tool(tool_call.name):
            available = ", ".join(s["name"] for s in self._tool_registry.get_tool_schemas())
            return ToolResult(
                output="",
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                success=False,
                error=build_error(
                    TOOL_NOT_FOUND,
                    f"Tool '{tool_call.name}' does not exist. Available: {available}.",
                ),
            )

        if self._forbidden_tools and tool_call.name in self._forbidden_tools:
            return ToolResult(
                output="",
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
                success=False,
                error=build_error(
                    TOOL_NOT_FOUND,
                    f"Tool '{tool_call.name}' is forbidden.",
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
                    f"Tool '{tool_call.name}' missing required args: {', '.join(missing)}.",
                ),
            )

        return None

    def _knowledge_search_fallback(self, tool_call: ToolCall) -> ToolResult | None:
        query = str(tool_call.arguments.get("query", "")).strip()
        if not query:
            return None
        try:
            entries = self._knowledge_loader.load(query)
        except Exception:
            return None
        if not entries:
            return None
        results = [{"rank": i + 1, "content": e.content, "tags": list(e.tags)} for i, e in enumerate(entries)]
        return ToolResult(
            output=json.dumps(
                {"source": "knowledge_base", "query": query, "result_count": len(results), "results": results},
                ensure_ascii=False,
            ),
            llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
            success=True,
        )

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
