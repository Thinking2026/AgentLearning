from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agent.strategy.decision import StrategyDecision
from schemas import ChatMessage, LLMRequest, LLMResponse

if TYPE_CHECKING:
    from agent.agent_executor import AgentExecutor


class Strategy(ABC):
    @abstractmethod
    def init_context(self, executor: AgentExecutor) -> None:
        """Called once after construction to set strategy-specific system prompt."""
        raise NotImplementedError

    @abstractmethod
    def build_llm_request(
        self,
        system_prompt: str,
        conversation: list[ChatMessage],
        tool_schemas: list[dict[str, Any]],
    ) -> LLMRequest:
        """Format conversation into an LLMRequest for this reasoning mode."""
        raise NotImplementedError

    @abstractmethod
    def parse_llm_response(self, response: LLMResponse) -> StrategyDecision:
        """Parse an LLMResponse into a structured decision."""
        raise NotImplementedError

    @abstractmethod
    def format_tool_observation(
        self,
        tool_name: str,
        output: str,
        llm_raw_tool_call_id: str | None,
    ) -> ChatMessage:
        """Format a tool result as a conversation message."""
        raise NotImplementedError
