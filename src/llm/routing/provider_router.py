from __future__ import annotations

from dataclasses import dataclass, field

from schemas import LLMRequest, LLM_CONFIG_ERROR, build_error
from llm.llm_api import SingleProviderClient
from llm.registry import LLMProviderRegistry


@dataclass
class RoutingDecision:
    primary: SingleProviderClient
    fallbacks: list[SingleProviderClient] = field(default_factory=list)


class LLMProviderRouter:
    """Returns a routing decision (primary + fallbacks) for each request."""

    def __init__(
        self,
        registry: LLMProviderRegistry,
        priority_chain: list[str],
        enable_fallback: bool = False,
    ) -> None:
        if not priority_chain:
            raise build_error(LLM_CONFIG_ERROR, "priority_chain cannot be empty")
        self._clients: list[SingleProviderClient] = [
            SingleProviderClient(registry.get(name))
            for name in priority_chain
        ]
        self._enable_fallback = enable_fallback

    def route(self, request: LLMRequest) -> RoutingDecision:  # noqa: ARG002
        primary = self._clients[0]
        fallbacks = self._clients[1:] if self._enable_fallback else []
        return RoutingDecision(primary=primary, fallbacks=fallbacks)
