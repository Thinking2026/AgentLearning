from __future__ import annotations

from typing import Any

from schemas import ChatMessage, LLMRequest, LLMResponse


class MessageFormatter:
    def build_request(
        self,
        system_prompt: str,
        conversation: list[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> LLMRequest:
        return LLMRequest(
            system_prompt=system_prompt,
            messages=conversation,
            tools=tools,
        )

    def format_tool_observation(
        self,
        tool_name: str,
        output: str,
        llm_raw_tool_call_id: str | None = None,
    ) -> ChatMessage:
        return ChatMessage(
            role="tool",
            content=output,
            metadata={
                "tool_name": tool_name,
                "llm_raw_tool_call_id": llm_raw_tool_call_id,
            },
        )

    def parse_response(self, response: LLMResponse) -> LLMResponse:
        return response
