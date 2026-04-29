from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import StepExecutionId, TaskId, TaskPlanId, TaskStepId, ToolCallId
from task.models.entities import DomainRuleViolation


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _event(event_type: type[DomainEvent], **kwargs: Any) -> DomainEvent:
    return event_type(event_type="", aggregate_id="", **kwargs)


class ReasoningMode(str, Enum):
    REACT = "react"
    DIRECT = "direct"


class StepExecutionStatus(str, Enum):
    STARTED = "Started"
    CONTEXT_READY = "ContextReady"
    REASONING = "Reasoning"
    GOAL_ACHIEVED = "GoalAchieved"
    GOAL_UNACHIEVABLE = "GoalUnachievable"
    RESULT_PRODUCED = "ResultProduced"
    EVALUATED = "Evaluated"


class ToolFailureClass(str, Enum):
    RETRYABLE = "A"
    RECOVERABLE_LATER = "B"
    UNRECOVERABLE = "C"


@dataclass
class StepExecutionStarted(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    task_step_id: TaskStepId = field(default="")
    goal: str = field(default="")
    reasoning_mode: ReasoningMode = field(default=ReasoningMode.REACT)

    def __post_init__(self) -> None:
        self.event_type = "StepExecutionStarted"
        self.aggregate_id = self.step_execution_id


@dataclass
class ReasoningStarted(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    round_index: int = field(default=0)
    context_version: int = field(default=0)
    model_name: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "ReasoningStarted"
        self.aggregate_id = self.step_execution_id


@dataclass
class LLMResponseReceived(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    round_index: int = field(default=0)
    response: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "LLMResponseReceived"
        self.aggregate_id = self.step_execution_id


@dataclass
class ReasoningCompleted(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    round_index: int = field(default=0)
    goal_achieved: bool = field(default=False)
    tool_call_requested: bool = field(default=False)

    def __post_init__(self) -> None:
        self.event_type = "ReasoningCompleted"
        self.aggregate_id = self.step_execution_id


@dataclass
class StepGoalAchieved(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "StepGoalAchieved"
        self.aggregate_id = self.step_execution_id


@dataclass
class StepGoalUnachievable(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    reason: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "StepGoalUnachievable"
        self.aggregate_id = self.step_execution_id


@dataclass
class StepResultProduced(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    result: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "StepResultProduced"
        self.aggregate_id = self.step_execution_id


@dataclass
class StepResultEvaluated(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    result: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "StepResultEvaluated"
        self.aggregate_id = self.step_execution_id


@dataclass
class StepResultEvaluationFailed(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    feedback: str = field(default="")
    retryable: bool = field(default=True)

    def __post_init__(self) -> None:
        self.event_type = "StepResultEvaluationFailed"
        self.aggregate_id = self.step_execution_id


@dataclass
class ReusableKnowledgeLoaded(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    task_id: TaskId = field(default="")
    step_goal: str = field(default="")
    entries: tuple[str, ...] = field(default_factory=tuple)
    degraded: bool = field(default=False)

    def __post_init__(self) -> None:
        self.event_type = "ReusableKnowledgeLoaded"
        self.aggregate_id = self.step_execution_id


@dataclass
class ModelSelected(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    model_name: str = field(default="")
    context_window: int = field(default=0)

    def __post_init__(self) -> None:
        self.event_type = "ModelSelected"
        self.aggregate_id = self.step_execution_id


@dataclass
class ContextAssembled(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    version: int = field(default=1)
    token_count: int = field(default=0)

    def __post_init__(self) -> None:
        self.event_type = "ContextAssembled"
        self.aggregate_id = self.step_execution_id


@dataclass
class ContextTruncated(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    version: int = field(default=1)
    original_token_count: int = field(default=0)
    token_count: int = field(default=0)

    def __post_init__(self) -> None:
        self.event_type = "ContextTruncated"
        self.aggregate_id = self.step_execution_id


@dataclass
class ToolResultInjected(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    version: int = field(default=1)
    tool_call_id: ToolCallId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "ToolResultInjected"
        self.aggregate_id = self.step_execution_id


@dataclass
class ToolCallRequested(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    tool_call_id: ToolCallId = field(default="")
    tool_name: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "ToolCallRequested"
        self.aggregate_id = self.step_execution_id


@dataclass
class ToolCallDispatched(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    tool_call_id: ToolCallId = field(default="")
    tool_name: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "ToolCallDispatched"
        self.aggregate_id = self.step_execution_id


@dataclass
class ToolCallSucceeded(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    tool_call_id: ToolCallId = field(default="")
    result: Any = field(default=None)

    def __post_init__(self) -> None:
        self.event_type = "ToolCallSucceeded"
        self.aggregate_id = self.step_execution_id


@dataclass
class ToolCallFailed(DomainEvent):
    step_execution_id: StepExecutionId = field(default="")
    tool_call_id: ToolCallId = field(default="")
    failure_class: ToolFailureClass = field(default=ToolFailureClass.RECOVERABLE_LATER)
    error: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "ToolCallFailed"
        self.aggregate_id = self.step_execution_id


@dataclass(frozen=True)
class ReasoningResult:
    response: str
    goal_achieved: bool = False
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepExecution(AggregateRoot):
    """Agent execution aggregate for a single TaskStep reasoning loop."""

    id: StepExecutionId
    task_step_id: TaskStepId
    plan_id: TaskPlanId
    goal: str
    reasoning_mode: ReasoningMode
    tool_set: tuple[str, ...]
    status: StepExecutionStatus = StepExecutionStatus.STARTED
    max_reasoning_rounds: int = 8
    max_tool_calls: int = 8
    max_result_retries: int = 2
    reasoning_round: int = 0
    tool_call_count: int = 0
    result_retry_count: int = 0
    latest_context_version: int | None = None
    result: str | None = None

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def start(
        cls,
        step_id: TaskStepId,
        goal: str,
        reasoning_mode: ReasoningMode,
        tool_set: list[str] | tuple[str, ...],
        plan_id: TaskPlanId,
        step_execution_id: StepExecutionId | None = None,
    ) -> StepExecution:
        if not goal.strip():
            raise DomainRuleViolation("step execution goal must not be empty")
        execution = cls(
            id=step_execution_id or StepExecutionId(_new_id("step_exec")),
            task_step_id=step_id,
            plan_id=plan_id,
            goal=goal,
            reasoning_mode=reasoning_mode,
            tool_set=tuple(tool_set),
        )
        execution._record(
            _event(
                StepExecutionStarted,
                step_execution_id=execution.id,
                task_step_id=step_id,
                goal=goal,
                reasoning_mode=reasoning_mode,
            )
        )
        return execution

    def mark_context_ready(self, version: int) -> None:
        self._ensure_not_terminal()
        self.latest_context_version = version
        self.status = StepExecutionStatus.CONTEXT_READY

    def run_reasoning(self, context_version: int, model_name: str, result: ReasoningResult) -> None:
        self._ensure_not_terminal()
        if self.status != StepExecutionStatus.CONTEXT_READY:
            raise DomainRuleViolation("reasoning requires assembled context")
        if self.latest_context_version != context_version:
            raise DomainRuleViolation("reasoning must use the latest context version")
        if self.reasoning_round >= self.max_reasoning_rounds:
            self.fail("reasoning round limit exceeded")
            return
        self.reasoning_round += 1
        self.status = StepExecutionStatus.REASONING
        self._record(
            _event(
                ReasoningStarted,
                step_execution_id=self.id,
                round_index=self.reasoning_round,
                context_version=context_version,
                model_name=model_name,
            )
        )
        self._record(_event(LLMResponseReceived, step_execution_id=self.id, round_index=self.reasoning_round, response=result.response))
        self._record(
            _event(
                ReasoningCompleted,
                step_execution_id=self.id,
                round_index=self.reasoning_round,
                goal_achieved=result.goal_achieved,
                tool_call_requested=result.tool_name is not None,
            )
        )
        self.status = StepExecutionStatus.CONTEXT_READY

    def count_tool_call_or_fail(self) -> bool:
        self._ensure_not_terminal()
        if self.tool_call_count >= self.max_tool_calls:
            self.fail("tool call limit exceeded")
            return False
        self.tool_call_count += 1
        return True

    def complete(self, result: str) -> None:
        self._ensure_not_terminal()
        self.status = StepExecutionStatus.GOAL_ACHIEVED
        self._record(_event(StepGoalAchieved, step_execution_id=self.id))
        self.result = result
        self.status = StepExecutionStatus.RESULT_PRODUCED
        self._record(_event(StepResultProduced, step_execution_id=self.id, result=result))

    def fail(self, reason: str) -> None:
        self._ensure_not_terminal()
        self.status = StepExecutionStatus.GOAL_UNACHIEVABLE
        self._record(_event(StepGoalUnachievable, step_execution_id=self.id, reason=reason))

    def evaluate_result(self, passed: bool, feedback: str = "") -> None:
        if self.status != StepExecutionStatus.RESULT_PRODUCED or self.result is None:
            raise DomainRuleViolation("result evaluation requires produced result")
        if passed:
            self.status = StepExecutionStatus.EVALUATED
            self._record(_event(StepResultEvaluated, step_execution_id=self.id, result=self.result))
            return
        self.result_retry_count += 1
        retryable = self.result_retry_count <= self.max_result_retries
        self._record(
            _event(
                StepResultEvaluationFailed,
                step_execution_id=self.id,
                feedback=feedback,
                retryable=retryable,
            )
        )
        if not retryable:
            self.fail("step result evaluation retry limit exceeded")

    def _ensure_not_terminal(self) -> None:
        if self.status in {StepExecutionStatus.GOAL_UNACHIEVABLE, StepExecutionStatus.EVALUATED}:
            raise DomainRuleViolation("terminal step execution cannot accept this command")


@dataclass(frozen=True)
class ContextMessage:
    role: str
    content: str

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content.split()))


@dataclass
class ContextManager(AggregateRoot):
    """Owns assembled context versions and tool-result injection."""

    step_execution_id: StepExecutionId
    version: int = 0
    messages: tuple[ContextMessage, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def assemble(
        cls,
        execution_id: StepExecutionId,
        history: list[ContextMessage] | tuple[ContextMessage, ...],
        current_input: ContextMessage,
        knowledge: list[str] | tuple[str, ...],
        preference: dict[str, str],
        model_context_window: int,
        system_prompt: ContextMessage | None = None,
    ) -> ContextManager:
        if model_context_window <= 0:
            raise DomainRuleViolation("model context window must be positive")
        system = system_prompt or ContextMessage("system", "You are a helpful agent.")
        preference_msg = ContextMessage("system", f"User preferences: {preference}") if preference else None
        knowledge_msg = ContextMessage("system", "\n".join(knowledge)) if knowledge else None
        fixed = tuple(message for message in (system, preference_msg, knowledge_msg, current_input) if message is not None)
        history_messages = tuple(history)
        messages = history_messages + fixed
        original_tokens = _token_count(messages)
        truncated = False
        while _token_count(messages) > model_context_window and history_messages:
            truncated = True
            history_messages = history_messages[1:]
            messages = history_messages + fixed
        if _token_count(messages) > model_context_window:
            raise DomainRuleViolation("fixed context exceeds selected model window")
        manager = cls(step_execution_id=execution_id, version=1, messages=messages)
        manager._record(_event(ContextAssembled, step_execution_id=execution_id, version=1, token_count=_token_count(messages)))
        if truncated:
            manager._record(
                _event(
                    ContextTruncated,
                    step_execution_id=execution_id,
                    version=1,
                    original_token_count=original_tokens,
                    token_count=_token_count(messages),
                )
            )
        return manager

    def inject_tool_result(self, tool_call_id: ToolCallId, content: str) -> None:
        self.version += 1
        self.messages = self.messages + (ContextMessage("tool", content),)
        self._record(_event(ToolResultInjected, step_execution_id=self.step_execution_id, version=self.version, tool_call_id=tool_call_id))


@dataclass
class KnowledgeLoader(AggregateRoot):
    """Loads reusable knowledge; failures degrade to an empty result."""

    step_execution_id: StepExecutionId
    task_id: TaskId
    entries: tuple[str, ...] = field(default_factory=tuple)
    degraded: bool = False

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def load(
        cls,
        task_id: TaskId,
        step_goal: str,
        entries: list[str] | tuple[str, ...] | None = None,
        error: Exception | None = None,
        step_execution_id: StepExecutionId | None = None,
    ) -> KnowledgeLoader:
        execution_id = step_execution_id or StepExecutionId(_new_id("step_exec"))
        loaded_entries = tuple(entries or ()) if error is None else ()
        loader = cls(step_execution_id=execution_id, task_id=task_id, entries=loaded_entries, degraded=error is not None)
        loader._record(
            _event(
                ReusableKnowledgeLoaded,
                step_execution_id=execution_id,
                task_id=task_id,
                step_goal=step_goal,
                entries=loaded_entries,
                degraded=error is not None,
            )
        )
        return loader


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    context_window: int
    latency_rank: int = 0


@dataclass
class ModelRouter(AggregateRoot):
    """Selects a model without leaking routing policy into business aggregates."""

    step_execution_id: StepExecutionId
    candidates: tuple[ModelCandidate, ...]
    selected: ModelCandidate | None = None

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    def select(self, reasoning_mode: ReasoningMode, required_context_tokens: int) -> ModelCandidate:
        if required_context_tokens <= 0:
            raise DomainRuleViolation("required context tokens must be positive")
        eligible = [candidate for candidate in self.candidates if candidate.context_window >= required_context_tokens]
        if not eligible:
            raise DomainRuleViolation("no model satisfies the current context token requirement")
        selected = sorted(eligible, key=lambda candidate: (candidate.latency_rank, candidate.context_window))[0]
        self.selected = selected
        self._record(
            _event(
                ModelSelected,
                step_execution_id=self.step_execution_id,
                model_name=selected.name,
                context_window=selected.context_window,
            )
        )
        return selected


class ToolCallStatus(str, Enum):
    REQUESTED = "Requested"
    DISPATCHED = "Dispatched"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"


@dataclass
class ToolCallRecord:
    id: ToolCallId
    tool_name: str
    args: dict[str, Any]
    status: ToolCallStatus
    result: Any = None
    error: str | None = None


@dataclass
class ToolCallOrchestrator(AggregateRoot):
    """Serial, idempotent tool-call boundary for a StepExecution."""

    step_execution_id: StepExecutionId
    allowed_tools: tuple[str, ...]
    in_flight_call_id: ToolCallId | None = None
    calls: dict[ToolCallId, ToolCallRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    def dispatch(self, tool_name: str, args: dict[str, Any], tool_call_id: ToolCallId | None = None) -> ToolCallId:
        if self.in_flight_call_id is not None:
            raise DomainRuleViolation("tool calls must be dispatched serially")
        if tool_name not in self.allowed_tools:
            raise DomainRuleViolation(f"tool is not allowed: {tool_name}")
        call_id = tool_call_id or ToolCallId(_new_id("tool_call"))
        record = ToolCallRecord(id=call_id, tool_name=tool_name, args=dict(args), status=ToolCallStatus.REQUESTED)
        self.calls[call_id] = record
        self._record(_event(ToolCallRequested, step_execution_id=self.step_execution_id, tool_call_id=call_id, tool_name=tool_name))
        record.status = ToolCallStatus.DISPATCHED
        self.in_flight_call_id = call_id
        self._record(_event(ToolCallDispatched, step_execution_id=self.step_execution_id, tool_call_id=call_id, tool_name=tool_name))
        return call_id

    def handle_result(
        self,
        tool_call_id: ToolCallId,
        *,
        result: Any = None,
        error: str | None = None,
        failure_class: ToolFailureClass | None = None,
    ) -> None:
        record = self.calls.get(tool_call_id)
        if record is None:
            raise DomainRuleViolation("unknown tool call result")
        if record.status in {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED}:
            return
        if self.in_flight_call_id != tool_call_id:
            raise DomainRuleViolation("tool result does not match the in-flight call")
        if error is None:
            record.status = ToolCallStatus.SUCCEEDED
            record.result = result
            self.in_flight_call_id = None
            self._record(_event(ToolCallSucceeded, step_execution_id=self.step_execution_id, tool_call_id=tool_call_id, result=result))
            return
        if failure_class == ToolFailureClass.RETRYABLE:
            record.result = error
            self.in_flight_call_id = None
            return
        if failure_class is None:
            raise DomainRuleViolation("non-retryable tool failure requires failure class")
        record.status = ToolCallStatus.FAILED
        record.error = error
        self.in_flight_call_id = None
        self._record(
            _event(
                ToolCallFailed,
                step_execution_id=self.step_execution_id,
                tool_call_id=tool_call_id,
                failure_class=failure_class,
                error=error,
            )
        )


def _token_count(messages: tuple[ContextMessage, ...]) -> int:
    return sum(message.token_estimate for message in messages)
