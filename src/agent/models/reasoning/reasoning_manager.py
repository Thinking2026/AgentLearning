from __future__ import annotations

from typing import TYPE_CHECKING

from agent.models.reasoning.decision import NextDecision

if TYPE_CHECKING:
    from agent.models.context.manager import ContextManager
    from agent.models.reasoning.strategy import Strategy
    from llm.llm_gateway import LLMGateway
    from schemas import LLMMessage, ToolCall, ToolResult
    from tools.tool_registry import ToolRegistry


class ReasoningManager:
    """Entity responsible for executing a single LLM reasoning step.

    Delegates request building and response parsing to the injected Strategy.
    Does not handle tool execution, context writes, or provider switching —
    those are the responsibility of StageExecutor.
    """

    def __init__(self, llm_gateway: LLMGateway, strategy: Strategy) -> None:
        self._llm_gateway = llm_gateway
        self._strategy = strategy

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def reason_once(
        self,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        selected_tool_names: list[str] | None = None,
        provider_name: str | None = None,
    ) -> NextDecision:
        """Execute one reasoning step.

        1. Build LLMRequest from context_manager (assembles + truncates if needed).
        2. Apply strategy-specific transformations (e.g. prepend ReAct system prompt).
        3. Call LLMGateway.generate().
        4. Parse LLMResponse into a NextDecision via strategy.

        Any LLMError raised by the gateway propagates to the caller (StageExecutor).
        """
        effective_provider = provider_name or self._llm_gateway.provider_name
        raw_request = context_manager.get_context_window(effective_provider)
        request = self._strategy.build_llm_request(raw_request)
        response = self._llm_gateway.generate(request)
        return self._strategy.parse_llm_response(response)

    def set_llm_gateway(self, llm_gateway: LLMGateway) -> None:
        """Replace the current gateway (called by StageExecutor on provider fallback)."""
        self._llm_gateway = llm_gateway

    def format_tool_observation(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> LLMMessage:
        """Format a ToolResult for context injection using the active strategy."""
        return self._strategy.format_tool_observation(tool_call, result)
