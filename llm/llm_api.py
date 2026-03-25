from __future__ import annotations

from abc import ABC, abstractmethod

from schemas import LLMRequest, LLMResponse


class BaseLLMClient(ABC):
    provider_name: str = "base"

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
