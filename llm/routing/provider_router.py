from __future__ import annotations

from schemas import LLMRequest, LLM_CONFIG_ERROR, RoutingDecision, build_error
from llm.llm_api import RetryConfig, SingleProviderClient
from llm.registry import LLMProviderRegistry


class LLMProviderRouter:
    """Returns a routing decision (primary + fallbacks) for each request.

    Does not call generate() — the caller is responsible for executing
    the providers and deciding whether/how to fall back.
    """

    def __init__(
        self,
        registry: LLMProviderRegistry,
        priority_chain: list[str],
        retry_config: RetryConfig,
        enable_fallback: bool = False,
    ) -> None:
        if not priority_chain:
            raise build_error(LLM_CONFIG_ERROR, "priority_chain cannot be empty")
        self._clients: list[SingleProviderClient] = [
            SingleProviderClient(registry.get(name), retry_config)
            for name in priority_chain
        ]
        self._enable_fallback = enable_fallback

    def route(self, request: LLMRequest) -> RoutingDecision:  # noqa: ARG002
        """Return which provider to use and which to fall back to for this request."""
        primary = self._clients[0]
        fallbacks = self._clients[1:] if self._enable_fallback else []
        return RoutingDecision(primary=primary, fallbacks=fallbacks)
