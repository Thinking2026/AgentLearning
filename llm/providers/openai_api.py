from __future__ import annotations

import json
import os

from llm.llm_api import BaseLLMClient
from schemas import (
    ChatMessage,
    LLMRequest,
    LLMResponse,
    LLM_CONFIG_ERROR,
    LLM_RESPONSE_ERROR,
    LLM_RESPONSE_PARSE_ERROR,
    ToolCall,
    build_error,
)


class OpenAILLMClient(BaseLLMClient):
    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._model = model
        self._init_http(
            base_url=base_url,
            default_headers={
                "Authorization": f"Bearer {api_key}",
                **(extra_headers or {}),
            },
            timeout=timeout,
        )

    @classmethod
    def from_settings(
        cls,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> "OpenAILLMClient":
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise build_error(LLM_CONFIG_ERROR, "Missing API key for OpenAI client.")
        return cls(
            api_key=resolved_api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        last_message = request.messages[-1].content if request.messages else ""
        with self._start_span(
            "llm.generate",
            attributes={
                "provider": self.provider_name,
                "model": self._model,
                "message_count": len(request.messages),
                "last_user_message": last_message,
            },
        ) as span:
            payload = {
                "model": self._model,
                "messages": self._serialize_messages(request),
            }
            tools = self._serialize_tools(request.tools)
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            response_data = self._post_json("/chat/completions", payload)
            response = self._parse_chat_completion(response_data)
            usage = response_data.get("usage") or {}
            span.add_attributes(
                {
                    "finish_reason": response.finish_reason,
                    "tool_calls_count": len(response.tool_calls),
                    "tool_calls": [
                        {"name": tc.name, "llm_raw_tool_call_id": tc.llm_raw_tool_call_id}
                        for tc in response.tool_calls
                    ],
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "response_text": response.assistant_message.content,
                }
            )
            return response

    def _post_json(self, path: str, payload: dict) -> dict:
        return self._http.post_json(path, payload)

    @staticmethod
    def _serialize_messages(request: LLMRequest) -> list[dict]:
        serialized_messages: list[dict] = [{"role": "system", "content": request.system_prompt}]
        for message in request.messages:
            serialized = {"role": message.role, "content": message.content}
            if message.role == "assistant":
                tool_calls = message.metadata.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    serialized["tool_calls"] = OpenAILLMClient._serialize_assistant_tool_calls(
                        tool_calls
                    )
            if message.role == "tool":
                tool_call_id = message.metadata.get("llm_raw_tool_call_id")
                if tool_call_id:
                    serialized["tool_call_id"] = tool_call_id
            serialized_messages.append(serialized)
        return serialized_messages

    @staticmethod
    def _serialize_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _serialize_assistant_tool_calls(tool_calls: list[dict]) -> list[dict]:
        serialized_tool_calls: list[dict] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = tool_call.get("llm_raw_tool_call_id")
            tool_name = tool_call.get("name")
            arguments = tool_call.get("arguments") or {}
            if not isinstance(tool_call_id, str) or not isinstance(tool_name, str):
                continue
            serialized_tool_calls.append(
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
        return serialized_tool_calls

    @classmethod
    def _parse_chat_completion(cls, response_data: dict) -> LLMResponse:
        choices = response_data.get("choices") or []
        if not choices:
            raise build_error(LLM_RESPONSE_ERROR, f"OpenAI API returned no choices: {response_data}")
        first_choice = choices[0]
        message = first_choice.get("message") or {}
        try:
            tool_calls = [
                ToolCall(
                    name=tool_call["function"]["name"],
                    arguments=json.loads(tool_call["function"]["arguments"] or "{}"),
                    llm_raw_tool_call_id=tool_call["id"],
                )
                for tool_call in (message.get("tool_calls") or [])
            ]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise build_error(
                LLM_RESPONSE_PARSE_ERROR,
                f"OpenAI API returned an invalid tool call payload: {exc}",
            ) from exc
        return LLMResponse(
            assistant_message=ChatMessage(
                role=message.get("role", "assistant"),
                content=message.get("content") or "",
                metadata={
                    "tool_calls_count": len(tool_calls),
                    "tool_calls": [
                        {
                            "name": tool_call.name,
                            "llm_raw_tool_call_id": tool_call.llm_raw_tool_call_id,
                            "arguments": tool_call.arguments,
                        }
                        for tool_call in tool_calls
                    ],
                },
            ),
            tool_calls=tool_calls,
            raw_response=response_data,
            finish_reason=first_choice.get("finish_reason", "stop"),
        )
