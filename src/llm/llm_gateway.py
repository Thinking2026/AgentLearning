from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from schemas import (
    AgentError,
    ErrorCategory,
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


class LLMGateway(BaseLLMClient):
    """Wraps a single provider with backoff-jitter retry for A-class errors.

    Retries on TRANSIENT and RATE_LIMITED errors up to _max_retries times.
    AUTH/CONFIG errors are not retried — they propagate immediately.
    All other errors propagate after exhausting retries.
    """

    def __init__(
        self,
        provider: BaseLLMClient,
        max_retries: int = 3,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
        timeout: float = 60.0,
    ) -> None:
        import random as _random
        self._provider = provider
        self._max_retries = max_retries
        self._retry_delays = retry_delays
        self._timeout = timeout
        self._random = _random

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        return self._provider.provider_name

    def generate(self, request: LLMRequest) -> LLMResponse:
        import time as _time
        logger = Logger.get_instance()
        logger.info(
            "LLM generate start",
            zap.any("provider", self._provider.provider_name),
            zap.any("messages", len(request.messages)),
        )
        last_exc: LLMError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._provider.generate(request)
            except LLMError as exc:
                last_exc = exc
                # AUTH/CONFIG: fatal for this provider, don't retry
                if exc.category in (ErrorCategory.AUTH, ErrorCategory.CONFIG):
                    raise
                # TRANSIENT / RATE_LIMITED: backoff and retry
                if exc.category in (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT):
                    if attempt < self._max_retries:
                        delay = exc.retry_after if exc.retry_after is not None else self._backoff(attempt)
                        logger.info(
                            "LLM retry backoff",
                            zap.any("provider", self._provider.provider_name),
                            zap.any("attempt", attempt + 1),
                            zap.any("delay_seconds", round(delay, 2)),
                        )
                        _time.sleep(delay)
                        continue
                # All other categories or retries exhausted: propagate
                raise
        # Should not reach here, but satisfy type checker
        if last_exc is not None:
            raise last_exc
        raise LLMError(LLMErrorCode.HTTP_5XX, "Unknown LLM error")

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        if attempt < len(self._retry_delays):
            cap = self._retry_delays[attempt]
        else:
            cap = self._retry_delays[-1] if self._retry_delays else 4.0
        return self._random.uniform(0, cap)

