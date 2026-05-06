from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.config import ConfigReader
from schemas import (
    AgentError,
    ErrorCategory,
    HttpError,
    LLMError,
    LLMErrorCode,
    UnifiedLLMRequest,
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

if TYPE_CHECKING:
    from llm.registry import LLMProviderRegistry


# ---------------------------------------------------------------------------
# HTTP error classification helpers (used by concrete providers)
# ---------------------------------------------------------------------------

_CONTEXT_TOO_LONG_HINTS = (
    "context_length_exceeded",
    "context too long",
    "maximum context length",
    "reduce the length",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "exceeds the model's context",
)

_CONTENT_FILTER_HINTS = (
    "content_filter",
    "content filter",
    "safety",
    "moderation",
    "policy violation",
    "harmful",
    "violates",
)

_QUOTA_HINTS = (
    "quota",
    "billing",
    "insufficient_quota",
    "exceeded your current quota",
    "account has been deactivated",
)

_OVERLOADED_HINTS = (
    "overloaded",
    "capacity",
    "server is busy",
    "try again later",
)


def _extract_retry_after(exc: HttpError) -> float | None:
    """Resolve retry delay in seconds from header or response body.

    Priority:
      1. Retry-After header (already parsed by HttpClient into seconds)
      2. Body JSON fields used by various providers:
         - OpenAI:    {"error": {"retry_after": <seconds float>}}
         - Anthropic: {"retry_after_ms": <milliseconds int>}  (rare, but documented)
         - Generic:   {"retry_after": <seconds float>}
    """
    if exc.retry_after is not None:
        return exc.retry_after
    try:
        body_json = json.loads(exc.body)
    except (json.JSONDecodeError, ValueError):
        return None
    # OpenAI nested form
    nested = body_json.get("error") if isinstance(body_json, dict) else None
    if isinstance(nested, dict):
        v = nested.get("retry_after")
        if v is not None:
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                pass
    if isinstance(body_json, dict):
        # Milliseconds variant (Anthropic documented field)
        v_ms = body_json.get("retry_after_ms")
        if v_ms is not None:
            try:
                return max(0.0, float(v_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        # Generic seconds variant
        v_s = body_json.get("retry_after")
        if v_s is not None:
            try:
                return max(0.0, float(v_s))
            except (TypeError, ValueError):
                pass
    return None


def classify_http_error(exc: HttpError, provider: str | None = None) -> LLMError:
    """Map an HttpError to a structured LLMError. Called by concrete providers."""
    body_lower = exc.body.lower()
    kw = dict(raw_status=exc.status, provider=provider)

    if exc.status == 429:
        retry_after = _extract_retry_after(exc)
        if any(h in body_lower for h in _QUOTA_HINTS):
            return LLMError(LLMErrorCode.QUOTA_EXCEEDED, f"Quota exceeded: {exc.body}", **kw)
        return LLMError(
            LLMErrorCode.RATE_LIMITED,
            f"Rate limited: {exc.body}",
            retry_after=retry_after,
            **kw,
        )

    if exc.status == 401:
        return LLMError(LLMErrorCode.AUTH_FAILED, f"Auth failed HTTP 401: {exc.body}", **kw)

    if exc.status == 403:
        return LLMError(LLMErrorCode.PERMISSION_DENIED, f"Permission denied HTTP 403: {exc.body}", **kw)

    if exc.status == 400:
        if any(h in body_lower for h in _CONTEXT_TOO_LONG_HINTS):
            return LLMError(LLMErrorCode.CONTEXT_TOO_LONG, f"Context too long: {exc.body}", **kw)
        if any(h in body_lower for h in _CONTENT_FILTER_HINTS):
            return LLMError(LLMErrorCode.INPUT_CONTENT_POLICY, f"Input content policy: {exc.body}", **kw)
        return LLMError(LLMErrorCode.INVALID_REQUEST, f"Invalid request HTTP 400: {exc.body}", **kw)

    if exc.status in {503, 529} or any(h in body_lower for h in _OVERLOADED_HINTS):
        retry_after = _extract_retry_after(exc)
        return LLMError(
            LLMErrorCode.PROVIDER_OVERLOADED,
            f"Provider overloaded HTTP {exc.status}: {exc.body}",
            retry_after=retry_after,
            **kw,
        )

    return LLMError(LLMErrorCode.HTTP_5XX, f"HTTP {exc.status}: {exc.body}", **kw)


_AGENT_ERROR_CODE_MAP: dict[str, LLMErrorCode] = {
    LLM_NETWORK_ERROR:        LLMErrorCode.NETWORK_ERROR,
    LLM_TIMEOUT:              LLMErrorCode.TIMEOUT,
    LLM_RESPONSE_PARSE_ERROR: LLMErrorCode.RESPONSE_PARSE_ERROR,
    LLM_RESPONSE_ERROR:       LLMErrorCode.RESPONSE_ERROR,
    LLM_CONFIG_ERROR:         LLMErrorCode.CONFIG_ERROR,
}


def classify_agent_error(exc: AgentError, provider: str | None = None) -> LLMError:
    """Map a legacy AgentError (from HttpClient) to a structured LLMError."""
    code = _AGENT_ERROR_CODE_MAP.get(exc.code, LLMErrorCode.RESPONSE_ERROR)
    return LLMError(code, exc.message, provider=provider)


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
    def generate(self, request: UnifiedLLMRequest) -> LLMResponse:
        raise NotImplementedError


class LLMGateway(BaseLLMClient):
    """Wraps a provider (resolved from LLMProviderRegistry) with backoff-jitter retry.

    Retries on TRANSIENT and RATE_LIMITED errors up to _max_retries times.
    AUTH/CONFIG errors are not retried — they propagate immediately.
    All other errors propagate after exhausting retries.
    """

    def __init__(
        self,
        registry: LLMProviderRegistry,
        config: ConfigReader,
        tracer: Tracer
    ) -> None:
        self._registry = registry
        self._config = config
        self._tracer = tracer
        self._max_retries = int(self._config.get("llm.retry.max_attempts", 3))
        self._retry_delays = self._config.retry_delays("llm.retry.backoff_seconds") or (1.0, 2.0, 4.0)
        import random as _random
        self._random = _random

    def generate(self, request: UnifiedLLMRequest, provider_name: str) -> LLMResponse:
        import time as _time
        logger = Logger.get_instance()
        provider = self._registry.get(provider_name)
        logger.info(
            "LLM generate start",
            zap.any("provider", provider_name),
            zap.any("messages", len(request.messages)),
        )
        last_exc: LLMError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return provider.generate(request)
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
                            zap.any("provider", provider_name),
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

