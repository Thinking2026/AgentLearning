from __future__ import annotations

from typing import TYPE_CHECKING

from agent.models.reasoning.decision import NextDecision

if TYPE_CHECKING:
    from agent.models.context.manager import ContextManager
    from agent.models.reasoning.strategy import Strategy
    from llm.llm_gateway import LLMGateway
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
    ) -> NextDecision:
        """Execute one reasoning step.

        1. Build LLMRequest from current context via strategy.
        2. Call LLMGateway.generate().
        3. Parse LLMResponse into a NextDecision via strategy.

        Any LLMError raised by the gateway propagates to the caller (StageExecutor).
        """
        request = self._strategy.build_llm_request(context_manager, tool_registry)
        response = self._llm_gateway.generate(request)
        decision = self._strategy.parse_llm_response(response)
        return decision

    def set_llm_gateway(self, llm_gateway: LLMGateway) -> None:
        """Replace the current gateway (called by StageExecutor on provider fallback)."""
        self._llm_gateway = llm_gateway
