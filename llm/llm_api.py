from __future__ import annotations

from abc import ABC, abstractmethod

from schemas import LLMRequest, LLMResponse
from tracing import SpanHandle, Tracer


class BaseLLMClient(ABC):
    provider_name: str = "base"

    def set_tracer(self, tracer: Tracer | None) -> "BaseLLMClient":
        self._tracer = tracer
        return self

    def _start_span(
        self,
        name: str,
        attributes: dict | None = None,
    ) -> SpanHandle:
        tracer = getattr(self, "_tracer", None)
        if tracer is None:
            return SpanHandle(None)
        return tracer.start_span(name=name, kind="llm", attributes=attributes)

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
