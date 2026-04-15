from __future__ import annotations

from typing import Any

from schemas import ChatMessage, LLMRequest, LLMResponse


class MessageFormatter:
    def __init__(self, max_messages: int | None = None) -> None:
        self._max_messages = max_messages

    def normalize_user_message(self, raw_text: str) -> ChatMessage:
        content = raw_text.strip()
        return ChatMessage(role="user", content=content)

    def build_request(
        self,
        system_prompt: str,
        conversation: list[ChatMessage],
        tools: list[dict[str, Any]],
        max_messages: int | None = None,
    ) -> LLMRequest:
        effective_max_messages = self._max_messages if max_messages is None else max_messages
        trimmed_conversation = self._trim_conversation(
            conversation=conversation,
            max_messages=effective_max_messages,
        )
        return LLMRequest(
            system_prompt=system_prompt,
            messages=trimmed_conversation,
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

    @staticmethod
    def _trim_conversation(
        conversation: list[ChatMessage],
        max_messages: int | None,
    ) -> list[ChatMessage]:
        if max_messages is None or max_messages <= 0:
            return list(conversation)
        if len(conversation) <= max_messages:
            return list(conversation)
        trimmed = list(conversation[-max_messages:])
        # A tail-trim may leave orphaned tool results at the start: the assistant
        # message that issued the tool_calls was cut off, but the tool role messages
        # that follow it were kept. OpenAI/Claude APIs reject this. Drop leading
        # tool messages until we reach a user or assistant message.
        while trimmed and trimmed[0].role == "tool":
            trimmed.pop(0)
        return trimmed
