from __future__ import annotations

from typing import Any

from schemas import LLMMessage, UnifiedLLMRequest, LLMResponse


class MessageFormatter:
    def build_request(
        self,
        system_prompt: str,
        conversation: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> UnifiedLLMRequest:
        return UnifiedLLMRequest(
            system_prompt=system_prompt,
            messages=conversation,
            tool_schemas=tools,
        )

    def format_tool_observation(
        self,
        tool_name: str,
        output: str,
        success: bool = True,
        llm_raw_tool_call_id: str | None = None,
    ) -> LLMMessage:
        return LLMMessage(
            role="tool",
            content=output,
            metadata={
                "tool_name": tool_name,
                "success": success,
                "llm_raw_tool_call_id": llm_raw_tool_call_id,
            },
        )

    def parse_response(self, response: LLMResponse) -> LLMResponse:
        return response
