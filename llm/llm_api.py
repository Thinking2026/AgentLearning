from __future__ import annotations

from abc import ABC, abstractmethod
import random
import time
from typing import TYPE_CHECKING

from schemas import (
    AgentError,
    ChatMessage,
    HttpError,
    LLMRequest,
    LLMResponse,
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

if TYPE_CHECKING:
    from llm.registry import LLMProviderRegistry

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
_RETRYABLE_CODES = {LLM_NETWORK_ERROR, LLM_TIMEOUT, LLM_RATE_LIMITED, LLM_HTTP_ERROR}

_RETRYABLE_HTTP_STATUS = {500, 502, 503, 504}

# Keywords in the error body that indicate context-too-long (400 subtype)
_CONTEXT_TOO_LONG_HINTS = (
    "context_length_exceeded",
    "context too long",
    "maximum context length",
    "reduce the length",
    "too many tokens",
)

# Keywords that mark a 400 as a hard invalid-request (not context length)
_INVALID_REQUEST_HINTS = (
    "invalid_request_error",
    "content_filter",
    "content filter",
)


def _classify_http_error(exc: HttpError) -> str:
    """Return an error code that describes how to handle this HTTP error."""
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
    """True when retrying would be pointless (auth errors, hard invalid requests)."""
    if exc.status in {401, 403}:
        return True
    if exc.status == 400:
        body_lower = exc.body.lower()
        return any(hint in body_lower for hint in _INVALID_REQUEST_HINTS)
    return False


class BaseLLMClient(ABC):
    provider_name: str = "base"

    def _init_http(
        self,
        base_url: str,
        default_headers: dict[str, str],
        timeout: float,
    ) -> None:
        self._http = HttpClient(
            base_url=base_url,
            default_headers=default_headers,
            timeout=timeout,
        )

    def set_tracer(self, tracer: Tracer | None) -> "BaseLLMClient":
        self._tracer = tracer
        return self

    def _start_span(
        self,
        name: str,
        attributes: dict | None = None,
    ) -> Span:
        tracer = getattr(self, "_tracer", None)
        if tracer is None:
            return Span(None)
        return tracer.start_span(name=name, type="llm", attributes=attributes)

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


class FallbackLLMClient(BaseLLMClient):
    provider_name = "fallback"

    def __init__(
        self,
        registry: LLMProviderRegistry,
        provider_priority: list[str],
        enable_provider_fallback: bool = False,
        retry_base: float = 0.5,
        retry_max_delay: float = 60.0,
        retry_max_attempts: int = 5,
    ) -> None:
        if not provider_priority:
            raise build_error(LLM_CONFIG_ERROR, "provider_priority cannot be empty")
        if retry_max_attempts <= 0:
            raise build_error(LLM_CONFIG_ERROR, "retry_max_attempts must be greater than 0")
        self._registry = registry
        self._provider_priority = provider_priority
        self._enable_provider_fallback = enable_provider_fallback
        self._retry_base = retry_base
        self._retry_max_delay = retry_max_delay
        self._retry_max_attempts = retry_max_attempts

    def _backoff_delay(self, attempt_idx: int) -> float:
        """Full jitter: wait = random(0, min(base * 2^n, max_delay))"""
        cap = min(self._retry_base * (2 ** attempt_idx), self._retry_max_delay)
        return random.uniform(0, cap)

    def generate(self, request: LLMRequest) -> LLMResponse:
        failure_messages: list[str] = []
        provider_names = (
            self._provider_priority
            if self._enable_provider_fallback
            else [self._provider_priority[0]]
        )

        for provider_name in provider_names:
            provider = self._registry.get(provider_name)
            current_request = request
            attempt_idx = 0
            while attempt_idx < self._retry_max_attempts:
                try:
                    return provider.generate(current_request)
                except HttpError as exc:
                    if _is_fatal_http(exc):#TODO 没有做好多模型封装
                        raise build_error(LLM_HTTP_ERROR, f"{provider_name}: HTTP {exc.status}: {exc.body}") from exc

                    code = _classify_http_error(exc)

                    if code == LLM_CONTEXT_TOO_LONG:
                        trimmed = self._try_trim_context(current_request)
                        if trimmed is None:
                            raise build_error(LLM_CONTEXT_TOO_LONG, f"{provider_name}: context too long and cannot be trimmed") from exc
                        current_request = trimmed
                        # context trim does not count as a retry attempt
                        failure_messages.append(f"{provider_name}[trim]: context trimmed and retrying")
                        continue

                    failure_messages.append(f"{provider_name}[attempt {attempt_idx + 1}/{self._retry_max_attempts}]: HTTP {exc.status}")
                    if attempt_idx < self._retry_max_attempts - 1:
                        if code == LLM_RATE_LIMITED and exc.retry_after is not None:
                            time.sleep(exc.retry_after)
                        elif code in _RETRYABLE_CODES:
                            time.sleep(self._backoff_delay(attempt_idx))
                    attempt_idx += 1

                except AgentError as exc:
                    repaired = self._try_parse_error_self_repair(provider, current_request, exc)
                    if repaired is not None:
                        return repaired

                    failure_messages.append(f"{provider_name}[attempt {attempt_idx + 1}/{self._retry_max_attempts}]: {exc}")
                    if attempt_idx < self._retry_max_attempts - 1:
                        if exc.code in _RETRYABLE_CODES:
                            time.sleep(self._backoff_delay(attempt_idx))
                        else:
                            break  # fatal AgentError — stop retrying this provider
                    attempt_idx += 1

                except Exception as exc:
                    failure_messages.append(f"{provider_name}[attempt {attempt_idx + 1}/{self._retry_max_attempts}]: {exc}")
                    if attempt_idx < self._retry_max_attempts - 1:
                        time.sleep(self._backoff_delay(attempt_idx))
                    attempt_idx += 1

        raise build_error(
            LLM_ALL_PROVIDERS_FAILED,
            "All attempted LLM providers failed. " + " | ".join(failure_messages),
        )

    def _try_trim_context(self, request: LLMRequest) -> LLMRequest | None:
        """Drop the oldest non-system messages to reduce context size.

        Returns a new request with fewer messages, or None if the context
        is already too small to trim further (fewer than 2 messages).
        """
        if len(request.messages) < 2:
            return None
        trimmed_messages = request.messages[2:]
        return LLMRequest(
            system_prompt=request.system_prompt,
            messages=trimmed_messages,
            tools=request.tools,
        )

    def _try_parse_error_self_repair(
        self,
        provider: BaseLLMClient,
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
