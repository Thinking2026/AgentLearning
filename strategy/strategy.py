from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from schemas import AgentExecutionResult, ChatMessage

if TYPE_CHECKING:
    from agent.agent_executor import AgentExecutor
    from tools import ToolRegistry


class Strategy(ABC):
    @abstractmethod
    def init_context(self, executor: AgentExecutor) -> None:
        """Called once by AgentExecutor after construction to set strategy-specific system prompt."""
        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        executor: AgentExecutor,
        tool_registry: ToolRegistry,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
        raise NotImplementedError
