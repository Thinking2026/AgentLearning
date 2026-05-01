from __future__ import annotations

from dataclasses import dataclass, field

from schemas import LLM_CONFIG_ERROR, build_error

# ---------------------------------------------------------------------------
# ModelSelector — TD-specified entity held by Pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingDecision:
    """Provider names only; Pipeline resolves to LLMGateway instances via registry."""
    primary: str
    fallbacks: list[str] = field(default_factory=list)


class ModelSelector:
    """Selects the primary provider and fallback chain for a task.

    Held by Pipeline; returns provider names that Pipeline resolves via
    LLMProviderRegistry.build_gateway().
    """

    def __init__(
        self,
        priority_chain: list[str],
        enable_fallback: bool = False,
    ) -> None:
        if not priority_chain:
            raise build_error(LLM_CONFIG_ERROR, "priority_chain cannot be empty")
        self._priority_chain = priority_chain
        self._enable_fallback = enable_fallback

    def route(
        self,
        model_hint: str | None = None,
        enable_fallback: bool | None = None,
        excluded_providers: set[str] | None = None,
    ) -> RoutingDecision:
        """Return primary provider name and fallback chain."""
        use_fallback = enable_fallback if enable_fallback is not None else self._enable_fallback
        excluded = excluded_providers or set()
        chain = [provider for provider in self._priority_chain if provider not in excluded]
        if not chain:
            raise build_error(LLM_CONFIG_ERROR, "no available providers after applying exclusions")
        if model_hint and model_hint in chain:
            chain = [model_hint] + [p for p in chain if p != model_hint]
        primary = chain[0]
        fallbacks = chain[1:] if use_fallback else []
        return RoutingDecision(primary=primary, fallbacks=fallbacks)
