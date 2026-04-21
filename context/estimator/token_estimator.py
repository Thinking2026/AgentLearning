import json
from abc import ABC, abstractmethod

from schemas.types import LLMRequest


class BaseTokenEstimator(ABC):
    @abstractmethod
    def estimate(self, request: LLMRequest) -> int: ...


class ClaudeTokenEstimator(BaseTokenEstimator):
    _CHARS_PER_TOKEN = 3.5

    def estimate(self, request: LLMRequest) -> int:
        text = request.system_prompt
        for msg in request.messages:
            text += msg.content
        if request.tools:
            text += json.dumps(request.tools)
        return int(len(text) / self._CHARS_PER_TOKEN)


class OpenAICompatibleTokenEstimator(BaseTokenEstimator):
    def __init__(self) -> None:
        import tiktoken
        self._enc = tiktoken.get_encoding("cl100k_base")

    def estimate(self, request: LLMRequest) -> int:
        text = request.system_prompt
        for msg in request.messages:
            text += msg.content
        if request.tools:
            text += json.dumps(request.tools)
        return len(self._enc.encode(text))


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
