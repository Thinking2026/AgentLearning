from __future__ import annotations

import json
from abc import ABC, abstractmethod

from schemas.types import LLMRequest


TokenEstimation = dict[str, int]
"""Keys: 'system', 'tools', 'user', 'assistant', 'tool', 'total'"""


class BaseTokenEstimator(ABC):
    @abstractmethod
    def estimate(self, request: LLMRequest) -> TokenEstimation: ...

    def _count(self, text: str) -> int:
        raise NotImplementedError


class ClaudeTokenEstimator(BaseTokenEstimator):
    _CHARS_PER_TOKEN = 3.5

    def _count(self, text: str) -> int:
        return int(len(text) / self._CHARS_PER_TOKEN)

    def estimate(self, request: LLMRequest) -> TokenEstimation:
        return _estimate(request, self._count)


class OpenAICompatibleTokenEstimator(BaseTokenEstimator):
    def __init__(self) -> None:
        import tiktoken
        self._enc = tiktoken.get_encoding("cl100k_base")

    def _count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def estimate(self, request: LLMRequest) -> TokenEstimation:
        return _estimate(request, self._count)


def _estimate(request: LLMRequest, count: ...) -> TokenEstimation:
    result: TokenEstimation = {"system": 0, "tools": 0, "user": 0, "assistant": 0, "tool": 0}

    result["system"] = count(request.system_prompt)
    result["tools"] = count(json.dumps(request.tools)) if request.tools else 0

    for msg in request.messages:
        result[msg.role] = result.get(msg.role, 0) + count(msg.content)

    result["total"] = sum(result.values())
    return result


class TokenEstimatorFactory:
    _REGISTRY: dict[str, type[BaseTokenEstimator]] = {
        "claude":   ClaudeTokenEstimator,
        "openai":   OpenAICompatibleTokenEstimator,
        "deepseek": OpenAICompatibleTokenEstimator,
        "qwen":     OpenAICompatibleTokenEstimator,
    }

    @classmethod
    def get_estimator(cls, provider_name: str) -> BaseTokenEstimator:
        estimator_cls = cls._REGISTRY.get(provider_name)
        if estimator_cls is None:
            raise ValueError(f"Unknown LLM provider: {provider_name!r}")
        return estimator_cls()
