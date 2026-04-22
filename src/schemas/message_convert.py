from __future__ import annotations

from schemas.types import LLMMessage, UIMessage


def ui_to_llm(msg: UIMessage) -> LLMMessage:
    """Convert a UIMessage to LLMMessage for adding to LLM conversation history."""
    return LLMMessage(role=msg.role, content=msg.content)


def llm_to_ui(msg: LLMMessage) -> UIMessage:
    """Convert a LLMMessage to UIMessage for sending to the UI layer."""
    role = "user" if msg.role == "user" else "assistant"
    return UIMessage(role=role, content=msg.content)
