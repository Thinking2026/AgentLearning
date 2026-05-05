from __future__ import annotations

from types import SimpleNamespace

from agent.models.context.manager import ContextManager
from agent.models.executor.stage_executor import StageExecutor, StageStatus
from agent.models.reasoning.decision import NextDecision, NextDecisionType
from schemas.errors import TOOL_EXECUTION_ERROR, build_error
from schemas.ids import PlanStepId, TaskId
from schemas.types import LLMMessage, ToolCall, ToolResult


class FakeStrategy:
    def format_tool_observation(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> LLMMessage:
        return LLMMessage(
            role="tool",
            content=result.output,
            metadata={
                "tool_name": tool_call.name,
                "success": result.success,
                "llm_raw_tool_call_id": result.llm_raw_tool_call_id,
            },
        )


class FakeReasoningManager:
    def __init__(self, decisions: list[NextDecision]) -> None:
        self._decisions = list(decisions)
        self._strategy = FakeStrategy()

    def reason_once(
        self,
        context_manager: ContextManager,
        tool_registry: object,
        selected_tool_names: list[str] | None = None,
        provider_name: str | None = None,
    ) -> NextDecision:
        return self._decisions.pop(0)

    def set_llm_gateway(self, llm_gateway: object) -> None:
        return None

    def format_tool_observation(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> LLMMessage:
        return self._strategy.format_tool_observation(tool_call, result)


class FakeToolRegistry:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[ToolCall] = []

    def execute(self, tool_call: ToolCall) -> ToolResult:
        self.calls.append(tool_call)
        return self.result

    def get_tool_schemas(self) -> list[dict]:
        return []

    def has_tool(self, name: str) -> bool:
        return True

    def validate_arguments(self, tool_call: ToolCall) -> list[str]:
        return []


class FakeKnowledgeLoader:
    def load(self, query: str) -> list[object]:
        return [SimpleNamespace(content=f"knowledge for {query}")]


def make_executor(
    decisions: list[NextDecision],
    tool_result: ToolResult | None = None,
) -> StageExecutor:
    return StageExecutor(
        reasoning_manager=FakeReasoningManager(decisions),
        context_manager=ContextManager(),
        tool_registry=FakeToolRegistry(tool_result or ToolResult(output="ok")),
        quality_evaluator=object(),
        knowledge_loader=FakeKnowledgeLoader(),
        max_iterations=5,
    )


def test_execute_stage_completes_with_final_answer() -> None:
    executor = make_executor([
        NextDecision(decision_type=NextDecisionType.FINAL_ANSWER, answer="done")
    ])

    stage = executor.execute_stage(
        task_id=TaskId("task-1"),
        plan_step_id=PlanStepId("step-1"),
        plan_step_goal="solve it",
        plan_step_description="finish the task",
    )

    assert stage.status == StageStatus.COMPLETED
    assert stage.result == "done"
    assert stage.iteration_count == 1


def test_tool_failure_is_injected_as_observation() -> None:
    tool_call = ToolCall(name="broken", arguments={}, llm_raw_tool_call_id="tc1")
    tool_error = build_error(TOOL_EXECUTION_ERROR, "boom")
    executor = make_executor(
        [
            NextDecision(
                decision_type=NextDecisionType.TOOL_CALL,
                tool_calls=[tool_call],
                assistant_message=LLMMessage(role="assistant", content="calling tool"),
            ),
            NextDecision(decision_type=NextDecisionType.FINAL_ANSWER, answer="recovered"),
        ],
        tool_result=ToolResult(
            output="",
            llm_raw_tool_call_id="tc1",
            success=False,
            error=tool_error,
        ),
    )

    stage = executor.execute_stage(
        task_id=TaskId("task-1"),
        plan_step_id=PlanStepId("step-1"),
        plan_step_goal="solve it",
        plan_step_description="finish the task",
    )

    assert stage.status == StageStatus.COMPLETED
    assert stage.result == "recovered"
    tool_messages = [
        message for message in executor.get_conversation_history() if message.role == "tool"
    ]
    assert tool_messages[-1].content == "Tool call failed: [TOOL_EXECUTION_ERROR] boom"
    assert tool_messages[-1].metadata["success"] is False


def test_clarification_needed_pauses_and_records_question() -> None:
    executor = make_executor([
        NextDecision(
            decision_type=NextDecisionType.CLARIFICATION_NEEDED,
            message="Which file should I use?",
        )
    ])

    stage = executor.execute_stage(
        task_id=TaskId("task-1"),
        plan_step_id=PlanStepId("step-1"),
        plan_step_goal="solve it",
        plan_step_description="finish the task",
    )

    assert stage.status == StageStatus.PAUSED
    assert stage.clarification_question == "Which file should I use?"
    assert executor.get_conversation_history()[-1].content == "Which file should I use?"
