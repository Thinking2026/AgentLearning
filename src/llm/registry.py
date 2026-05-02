from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from schemas import build_error

if TYPE_CHECKING:
    from llm.llm_gateway import BaseLLMClient, LLMGateway


class LLMProviderRegistry:
    def __init__(self, providers: Iterable[BaseLLMClient] | None = None) -> None:
        self._providers: dict[str, BaseLLMClient] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: BaseLLMClient) -> None:
        self._providers[provider.provider_name] = provider

    def get(self, provider_name: str) -> BaseLLMClient:
        try:
            return self._providers[provider_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._providers)) or "<none>"
            raise build_error(
                "LLM_PROVIDER_NOT_FOUND",
                f"Unknown LLM provider: {provider_name}. Available providers: {available}",
            ) from exc

    def list_providers(self) -> list[str]:
        return sorted(self._providers)

    def build_gateway(
        self,
        provider_name: str,
        max_retries: int = 3,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
        timeout: float = 60.0,
    ) -> LLMGateway:
        """Build an LLMGateway wrapping the named provider."""
        from llm.llm_gateway import LLMGateway as _LLMGateway
        return _LLMGateway(
            registry=self,
            provider_name=provider_name,
            max_retries=max_retries,
            retry_delays=retry_delays,
            timeout=timeout,
        )
