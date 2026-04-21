from __future__ import annotations

from schemas import (
    AgentError,
    LLMRequest,
    LLMResponse,
    LLM_ALL_PROVIDERS_FAILED,
    LLM_CONFIG_ERROR,
    ProviderFailure,
    build_error,
)
from llm.llm_api import RetryConfig, SingleProviderClient
from llm.registry import LLMProviderRegistry
from utils.log import Logger, zap


class LLMProviderRouter:
    """Selects a SingleProviderClient per request and handles cross-provider fallback."""

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

    def route(self, request: LLMRequest) -> LLMResponse:
        """Run inference, falling back across providers on ProviderFailure."""
        targets = self._clients if self._enable_fallback else self._clients[:1]
        current_request = request
        failures: list[str] = []
        logger = Logger.get_instance()

        logger.info(
            "LLMProviderRouter route start",
            zap.any("providers", [c.provider_name for c in targets]),
            zap.any("messages", len(current_request.messages)),
        )

        for client in targets:
            try:
                return client.generate(current_request)
            except ProviderFailure as exc:
                logger.error(
                    "Provider failed, trying next",
                    zap.any("provider", exc.provider_name),
                    zap.any("reason", str(exc)),
                )
                failures.append(f"{exc.provider_name}: {exc}")
                current_request = exc.final_request
            except AgentError:
                raise

        logger.error(
            "All LLM providers failed",
            zap.any("providers", [c.provider_name for c in targets]),
            zap.any("failures", failures),
        )
        raise build_error(
            LLM_ALL_PROVIDERS_FAILED,
            "All attempted LLM providers failed. " + " | ".join(failures),
        )
