from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import random
import time

from schemas import (
    AgentError,
    ChatMessage,
    HttpError,
    LLMRequest,
    LLMResponse,
    ProviderFailure,
    LLM_ALL_PROVIDERS_FAILED,
    LLM_CONFIG_ERROR,
    LLM_CONTEXT_TOO_LONG,
    LLM_HTTP_ERROR,
    LLM_NETWORK_ERROR,
    LLM_RATE_LIMITED,
    LLM_RESPONSE_ERROR,
    LLM_RESPONSE_PARSE_ERROR,
    LLM_TIMEOUT,
    build_error,
)
from tracing import Span, Tracer
from utils.http_client import HttpClient
from utils.log import Logger, zap


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
_RETRYABLE_CODES = {LLM_NETWORK_ERROR, LLM_TIMEOUT, LLM_RATE_LIMITED, LLM_HTTP_ERROR}

_RETRYABLE_HTTP_STATUS = {500, 502, 503, 504}

_CONTEXT_TOO_LONG_HINTS = (
    "context_length_exceeded",
    "context too long",
    "maximum context length",
    "reduce the length",
    "too many tokens",
)

_INVALID_REQUEST_HINTS = (
    "invalid_request_error",
    "content_filter",
    "content filter",
)

def _classify_http_error(exc: HttpError) -> str:
    body_lower = exc.body.lower()
    if exc.status == 429:
        return LLM_RATE_LIMITED
    if exc.status in _RETRYABLE_HTTP_STATUS:
        return LLM_HTTP_ERROR
    if exc.status == 400:
        if any(hint in body_lower for hint in _CONTEXT_TOO_LONG_HINTS):
            return LLM_CONTEXT_TOO_LONG
    return LLM_HTTP_ERROR


def _is_fatal_http(exc: HttpError) -> bool:
    """True when retrying the same provider is pointless; caller should switch provider."""
    if exc.status in {401, 403}:
        return True
    if exc.status == 400:
        body_lower = exc.body.lower()
        return any(hint in body_lower for hint in _INVALID_REQUEST_HINTS)
    return False


# ---------------------------------------------------------------------------
# Shared helpers (module-level so both layers can use them)
# ---------------------------------------------------------------------------

def _try_trim_context(request: LLMRequest) -> LLMRequest | None:
    """Drop the two oldest non-system messages. Returns None if already too short."""
    if len(request.messages) < 2:
        return None
    return LLMRequest(
        system_prompt=request.system_prompt,
        messages=request.messages[2:],
        tools=request.tools,
    )

def _try_parse_error_self_repair(
    provider: "BaseLLMClient",
    request: LLMRequest,
    error: AgentError,
) -> LLMResponse | None:
    if error.code not in {LLM_RESPONSE_PARSE_ERROR, LLM_RESPONSE_ERROR}:
        return None
    repair_prompt = (
        "Your previous output could not be parsed by the client. "
        "Please regenerate a valid response following the expected tool-call/text format. "
        "Below is the parser error and raw output details captured by client.\n\n"
        f"{error.message}"
    )
    repaired_request = LLMRequest(
        system_prompt=request.system_prompt,
        messages=[*request.messages, ChatMessage(role="user", content=repair_prompt)],
        tools=request.tools,
    )
    try:
        return provider.generate(repaired_request)
    except Exception:
        return None

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
# Layer 2: per-provider retry + fault tolerance
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    retry_base: float = 0.5
    retry_max_delay: float = 60.0
    retry_max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.retry_max_attempts <= 0:
            raise build_error(LLM_CONFIG_ERROR, "retry_max_attempts must be greater than 0")


class SingleProviderClient(BaseLLMClient):
    """Wraps a single provider and handles per-provider retry and fault tolerance.

    On exhaustion or provider-fatal errors, raises ProviderFailure (not AgentError),
    so the caller can switch to the next provider while preserving request state.
    Only truly request-fatal errors (e.g. context too long with no trim possible)
    are raised as AgentError to stop all providers immediately.
    """

    def __init__(self, provider: BaseLLMClient, retry_config: RetryConfig = field(default_factory=RetryConfig)) -> None:
        self._provider = provider
        self._retry_config = retry_config if isinstance(retry_config, RetryConfig) else RetryConfig()

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        return self._provider.provider_name

    def _backoff_delay(self, attempt_idx: int) -> float:
        cap = min(self._retry_config.retry_base * (2 ** attempt_idx), self._retry_config.retry_max_delay)
        return random.uniform(0, cap)

    def generate(self, request: LLMRequest) -> LLMResponse:
        current_request = request
        attempt_idx = 0
        failures: list[str] = []
        cfg = self._retry_config
        name = self._provider.provider_name
        logger = Logger.get_instance()

        logger.info(
            "LLM generate start",
            zap.any("provider", name),
            zap.any("messages", len(current_request.messages)),
        )

        while attempt_idx < cfg.retry_max_attempts:
            try:
                result = self._provider.generate(current_request)
                logger.info(
                    "LLM generate success",
                    zap.any("provider", name),
                    zap.any("attempt", attempt_idx + 1),
                    zap.any("finish_reason", result.finish_reason),
                )
                return result

            except HttpError as exc:
                if _is_fatal_http(exc):
                    logger.error(
                        "Provider fatal HTTP error, switching provider",
                        zap.any("provider", name),
                        zap.any("status", exc.status),
                        zap.any("body", exc.body),
                    )
                    raise ProviderFailure(name, f"HTTP {exc.status}: {exc.body}", current_request) from exc

                code = _classify_http_error(exc)

                if code == LLM_CONTEXT_TOO_LONG:
                    trimmed = _try_trim_context(current_request)
                    if trimmed is None:
                        logger.error(
                            "Context too long and cannot be trimmed",
                            zap.any("provider", name),
                            zap.any("messages", len(current_request.messages)),
                        )
                        raise build_error(
                            LLM_CONTEXT_TOO_LONG,
                            f"{name}: context too long and cannot be trimmed",
                        ) from exc
                    logger.info(
                        "Context trimmed, retrying",
                        zap.any("provider", name),
                        zap.any("remaining_messages", len(trimmed.messages)),
                    )
                    current_request = trimmed
                    failures.append(f"{name}[trim]: context trimmed, retrying")
                    continue

                failures.append(f"{name}[attempt {attempt_idx + 1}/{cfg.retry_max_attempts}]: HTTP {exc.status}")
                logger.error(
                    "Provider HTTP error, retrying",
                    zap.any("provider", name),
                    zap.any("status", exc.status),
                    zap.any("attempt", attempt_idx + 1),
                    zap.any("max_attempts", cfg.retry_max_attempts),
                )
                if attempt_idx < cfg.retry_max_attempts - 1:
                    if code == LLM_RATE_LIMITED and exc.retry_after is not None:
                        time.sleep(exc.retry_after)
                    elif code in _RETRYABLE_CODES:
                        time.sleep(self._backoff_delay(attempt_idx))
                attempt_idx += 1

            except AgentError as exc:
                resp_after_repaired = _try_parse_error_self_repair(self._provider, current_request, exc)#TODO repair修复逻辑没有对返回做错误分类处理
                if resp_after_repaired is not None:
                    logger.info(
                        "Self-repair succeeded",
                        zap.any("provider", name),
                        zap.any("error_code", exc.code),
                    )
                    return resp_after_repaired

                failures.append(f"{name}[attempt {attempt_idx + 1}/{cfg.retry_max_attempts}]: {exc}")
                logger.error(
                    "Provider AgentError, switching provider",
                    zap.any("provider", name),
                    zap.any("error_code", exc.code),
                    zap.any("error", str(exc)),
                    zap.any("attempt", attempt_idx + 1),
                )
                if exc.code in _RETRYABLE_CODES and attempt_idx < cfg.retry_max_attempts - 1:
                    time.sleep(self._backoff_delay(attempt_idx))
                    attempt_idx += 1
                else:
                    raise ProviderFailure(name, " | ".join(failures), current_request) from exc

            except Exception as exc:
                failures.append(f"{name}[attempt {attempt_idx + 1}/{cfg.retry_max_attempts}]: {exc}")
                logger.error(
                    "Provider unexpected error, retrying",
                    zap.any("provider", name),
                    zap.any("error", str(exc)),
                    zap.any("attempt", attempt_idx + 1),
                    zap.any("max_attempts", cfg.retry_max_attempts),
                )
                if attempt_idx < cfg.retry_max_attempts - 1:
                    time.sleep(self._backoff_delay(attempt_idx))
                attempt_idx += 1

        logger.error(
            "Provider retries exhausted, switching provider",
            zap.any("provider", name),
            zap.any("max_attempts", cfg.retry_max_attempts),
            zap.any("failures", failures),
        )
        raise ProviderFailure(name, " | ".join(failures), current_request)


# ---------------------------------------------------------------------------
# Layer 1: cross-provider fallback orchestrator
# ---------------------------------------------------------------------------

class ProviderFallbackClient(BaseLLMClient):
    """Tries providers in priority order, passing trimmed request state across switches.

    Each provider is a SingleProviderClient that handles its own retry logic.
    ProviderFailure from one provider causes a switch to the next, carrying
    the final request state (which may have been trimmed).
    AgentError (request-fatal) propagates immediately without trying other providers.
    """
    provider_name = "fallback"

    def __init__(
        self,
        clients: list[SingleProviderClient],
        enable_fallback: bool = False,
    ) -> None:
        if not clients:
            raise build_error(LLM_CONFIG_ERROR, "clients cannot be empty")
        self._clients = clients
        self._enable_fallback = enable_fallback

    def generate(self, request: LLMRequest) -> LLMResponse:
        current_request = request
        targets = self._clients if self._enable_fallback else self._clients[:1]
        failures: list[str] = []
        logger = Logger.get_instance()

        logger.info(
            "Fallback LLM generate start",
            zap.any("providers", [c.provider_name for c in targets]),
            zap.any("messages", len(current_request.messages)),
        )

        for client in targets:
            try:
                return client.generate(current_request)
            except ProviderFailure as exc:
                logger.error(
                    "Provider failed, switching to next",
                    zap.any("provider", exc.provider_name),
                    zap.any("reason", str(exc)),
                    zap.any("remaining_providers", [c.provider_name for c in targets if c.provider_name != exc.provider_name]),
                )
                failures.append(f"{exc.provider_name}: {exc}")
                current_request = exc.final_request

        logger.error(
            "All LLM providers failed",
            zap.any("providers", [c.provider_name for c in targets]),
            zap.any("failures", failures),
        )
        raise build_error(
            LLM_ALL_PROVIDERS_FAILED,
            "All attempted LLM providers failed. " + " | ".join(failures),
        )
