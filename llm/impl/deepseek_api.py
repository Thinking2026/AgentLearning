from __future__ import annotations

import os

from llm.impl.openai_api import OpenAILLMClient
from schemas import build_error


class DeepSeekLLMClient(OpenAILLMClient):
    provider_name = "deepseek"

    @classmethod
    def from_settings(
        cls,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.deepseek.com/v1",
        timeout: float = 60.0,
    ) -> "DeepSeekLLMClient":
        resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not resolved_api_key:
            raise build_error("LLM_CONFIG_ERROR", "Missing API key for DeepSeek client.")
        return cls(
            api_key=resolved_api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
