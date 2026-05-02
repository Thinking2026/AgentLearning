from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agent.models.reasoning.decision import NextDecision
from schemas import LLMMessage, LLMRequest, LLMResponse

if TYPE_CHECKING:
    from agent.models.context.manager import ContextWindow
    from schemas import ToolCall, ToolResult
    from tools import ToolRegistry


class Strategy(ABC):
    @abstractmethod
    def build_llm_request(
        self,
        context_window: ContextWindow,
        tool_registry: ToolRegistry,
        selected_tool_names: list[str] | None = None,
    ) -> LLMRequest:
        """Format conversation into an LLMRequest for this reasoning mode."""
        raise NotImplementedError

    @abstractmethod
    def parse_llm_response(self, response: LLMResponse) -> NextDecision:
        """Parse an LLMResponse into a structured decision."""
        raise NotImplementedError

    @abstractmethod
    def format_tool_observation(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> LLMMessage:
        """Format a tool result as a conversation message."""
        raise NotImplementedError
