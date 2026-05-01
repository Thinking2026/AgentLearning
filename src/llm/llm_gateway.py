from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from schemas import (
    AgentError,
    HttpError,
    LLMError,
    LLMErrorCode,
    LLMRequest,
    LLMResponse,
    LLM_CONFIG_ERROR,
    LLM_NETWORK_ERROR,
    LLM_RESPONSE_ERROR,
    LLM_RESPONSE_PARSE_ERROR,
    LLM_TIMEOUT,
    build_error,
)
from infra.observability.tracing import Span, Tracer
from utils.http.http_client import HttpClient
from utils.log.log import Logger, zap


# ---------------------------------------------------------------------------
# HTTP error classification helpers (used by concrete providers)
# ---------------------------------------------------------------------------

_CONTEXT_TOO_LONG_HINTS = (
    "context_length_exceeded",
    "context too long",
    "maximum context length",
    "reduce the length",
    "too many tokens",
)


def classify_http_error(exc: HttpError) -> LLMError:
    """Map an HttpError to a structured LLMError. Called by concrete providers."""
    body_lower = exc.body.lower()
    if exc.status == 429:
        return LLMError(LLMErrorCode.RATE_LIMITED, f"Rate limited: {exc.body}", retry_after=exc.retry_after)
    if exc.status in {401, 403}:
        return LLMError(LLMErrorCode.AUTH_FAILED, f"Auth failed HTTP {exc.status}: {exc.body}")
    if exc.status == 400 and any(h in body_lower for h in _CONTEXT_TOO_LONG_HINTS):
        return LLMError(LLMErrorCode.CONTEXT_TOO_LONG, f"Context too long: {exc.body}")
    return LLMError(LLMErrorCode.HTTP_5XX, f"HTTP {exc.status}: {exc.body}")


_AGENT_ERROR_CODE_MAP: dict[str, LLMErrorCode] = {
    LLM_NETWORK_ERROR:       LLMErrorCode.NETWORK_ERROR,
    LLM_TIMEOUT:             LLMErrorCode.TIMEOUT,
    LLM_RESPONSE_PARSE_ERROR: LLMErrorCode.RESPONSE_PARSE_ERROR,
    LLM_RESPONSE_ERROR:      LLMErrorCode.RESPONSE_ERROR,
    LLM_CONFIG_ERROR:        LLMErrorCode.CONFIG_ERROR,
}


def classify_agent_error(exc: AgentError) -> LLMError:
    """Map a legacy AgentError (from HttpClient) to a structured LLMError."""
    code = _AGENT_ERROR_CODE_MAP.get(exc.code, LLMErrorCode.RESPONSE_ERROR)
    return LLMError(code, exc.message)


# ---------------------------------------------------------------------------
# RetryConfig (used by AgentExecutor)
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    retry_base: float = 0.5
    retry_max_delay: float = 60.0
    retry_max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.retry_max_attempts <= 0:
            raise build_error(LLM_CONFIG_ERROR, "retry_max_attempts must be greater than 0")


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseLLMClient(ABC):
    provider_name: str = "base"

    def _init_http(self, base_url: str, default_headers: dict[str, str], timeout: float) -> None:
        self._http = HttpClient(base_url=base_url, default_headers=default_headers, timeout=timeout)

    def set_tracer(self, tracer: Tracer | None) -> "BaseLLMClient":
        self._tracer = tracer
        return self

    def _start_span(self, name: str, attributes: dict | None = None) -> Span:
        tracer = getattr(self, "_tracer", None)
        if tracer is None:
            return Span(None)
        return tracer.start_span(name=name, type="llm", attributes=attributes)

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SingleProviderClient — thin passthrough; retry logic lives in AgentExecutor
# ---------------------------------------------------------------------------

class LLMGateway(BaseLLMClient):
    """Delegates to a concrete provider. Raises LLMError on failure."""

    def __init__(self, provider: BaseLLMClient) -> None:
        self._provider = provider

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        return self._provider.provider_name

    def generate(self, request: LLMRequest) -> LLMResponse:
        logger = Logger.get_instance()
        logger.info(
            "LLM generate start",
            zap.any("provider", self._provider.provider_name),
            zap.any("messages", len(request.messages)),
        )
        return self._provider.generate(request)

