from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemas.task import ProviderCapabilities, ModelRoutingDecision, Task, TaskFeature
from schemas import LLM_CONFIG_ERROR, build_error

# Maps LLM-output complexity labels to TD cognitive-complexity tiers.
# Providers declare tiers in config; analysis outputs natural-language labels.
_COMPLEXITY_TO_TIERS: dict[str, list[str]] = {
    "simple":  ["L1", "L2", "simple"],
    "medium":  ["L2", "L3", "medium"],
    "complex": ["L3", "L4", "complex"],
}


# ---------------------------------------------------------------------------
# RoutingStrategy protocol — swap at any time
# ---------------------------------------------------------------------------

@runtime_checkable
class RoutingStrategy(Protocol):
    """Pluggable strategy: given analysis + capabilities, return an ordered provider list."""

    def select(
        self,
        task: Task,
        candidates: list[ProviderCapabilities],
    ) -> list[str]:
        """Return provider names in priority order (best first). Must be non-empty."""
        ...


# ---------------------------------------------------------------------------
# Built-in strategy 1: capability-match scoring (default)
# ---------------------------------------------------------------------------

class CapabilityMatchStrategy:
    """Score each provider against the task analysis and rank by score.

    Scoring rules (additive):
    +3  per matching best_scenario
    +2  per matching required_strength
    +2  if provider supports the task complexity tier
    -2  if context_size < min_context_size
    -1  if prefer_low_cost and cost_tier is "high"
    -1  if prefer_low_latency and latency_tier is not "fast"
    """

    def select(
        self,
        task: Task,
        candidates: list[ProviderCapabilities],
    ) -> list[str]:
        if not candidates:
            raise build_error(LLM_CONFIG_ERROR, "no provider candidates available")
        if task is None:
            return [c.name for c in candidates]

        accepted_tiers = _COMPLEXITY_TO_TIERS.get(task.complexity, [task.complexity])

        scored: list[tuple[int, str]] = []
        for cap in candidates:
            score = 0
            for scenario in task.feature.preferred_scenarios:
                if scenario in cap.best_scenarios:
                    score += 3
            for strength in task.feature.required_strengths:
                if strength in cap.top_strengths:
                    score += 2
            if any(t in cap.cognitive_complexity for t in accepted_tiers):
                score += 2
            if task.feature.min_context_size > 0 and cap.context_size < task.feature.min_context_size:
                score -= 2
            if task.feature.prefer_low_cost and cap.cost_tier == "high":
                score -= 1
            if task.feature.prefer_low_latency and cap.latency_tier != "fast":
                score -= 1
            scored.append((score, cap.name))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _, name in scored]

# ---------------------------------------------------------------------------
# Built-in strategy 3: cost/latency first (TD §按延迟与成本映射)
# ---------------------------------------------------------------------------

class CostLatencyStrategy:
    """Rank providers by cost and latency tiers, ignoring task semantics.

    Useful when the caller explicitly wants the cheapest or fastest provider
    regardless of capability fit.  Tie-break by original config order.

    cost_tier rank:  low=0, medium=1, high=2
    latency_tier rank: fast=0, medium=1, slow=2
    """

    _COST_RANK = {"low": 0, "medium": 1, "high": 2}
    _LATENCY_RANK = {"fast": 0, "medium": 1, "slow": 2}

    def __init__(self, weight_cost: float = 0.5, weight_latency: float = 0.5) -> None:
        self._wc = weight_cost
        self._wl = weight_latency

    def select(
        self,
        _analysis: TaskFeature | None,
        candidates: list[ProviderCapabilities],
    ) -> list[str]:
        def _rank(cap: ProviderCapabilities) -> float:
            c = self._COST_RANK.get(cap.cost_tier, 1)
            l = self._LATENCY_RANK.get(cap.latency_tier, 1)
            return self._wc * c + self._wl * l

        return [c.name for c in sorted(candidates, key=_rank)]


# ---------------------------------------------------------------------------
# ModelSelector — TD-specified entity held by Pipeline
# ---------------------------------------------------------------------------

class ModelSelector:
    """Selects the primary provider and fallback chain for a task.

    The selection algorithm is fully delegated to a RoutingStrategy, which can
    be replaced at any time via set_strategy().
    """

    def __init__(
        self,
        provider_capabilities: list[ProviderCapabilities],
        strategy: RoutingStrategy | None = None,
        enable_fallback: bool = False,
    ) -> None:
        if not provider_capabilities:
            raise build_error(LLM_CONFIG_ERROR, "provider_capabilities cannot be empty")
        self._capabilities = provider_capabilities
        self._strategy: RoutingStrategy = strategy or CapabilityMatchStrategy()
        self._enable_fallback = enable_fallback

    def set_strategy(self, strategy: RoutingStrategy) -> None:
        """Replace the routing strategy at runtime."""
        self._strategy = strategy

    def route(
        self,
        task: Task,
        enable_fallback: bool | None = None,
        excluded_providers: set[str] | None = None,
    ) -> ModelRoutingDecision:
        """Return primary provider name and fallback chain."""
        use_fallback = enable_fallback if enable_fallback is not None else self._enable_fallback
        excluded = excluded_providers or set()
        candidates = [c for c in self._capabilities if c.name not in excluded]
        if not candidates:
            raise build_error(LLM_CONFIG_ERROR, "no available providers after applying exclusions")

        ordered = self._strategy.select(task, candidates)
        if not ordered:
            raise build_error(LLM_CONFIG_ERROR, "routing strategy returned an empty provider list")

        primary = ordered[0]
        fallbacks = ordered[1:] if use_fallback else []
        return ModelRoutingDecision(primary=primary, fallbacks=fallbacks)

