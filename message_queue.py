from __future__ import annotations

import queue
from typing import Optional

from models import ChatMessage


class MessageQueue:
    def __init__(self) -> None:
        self._user_to_agent: queue.Queue[ChatMessage] = queue.Queue()
        self._agent_to_user: queue.Queue[ChatMessage] = queue.Queue()
        self._closed = False

    def send_user_message(self, message: ChatMessage) -> None:
        if self._closed:
            return
        self._user_to_agent.put(message)

    def get_user_message(self, timeout: float | None = None) -> Optional[ChatMessage]:
        return self._safe_get(self._user_to_agent, timeout)

    def send_agent_message(self, message: ChatMessage) -> None:
        if self._closed:
            return
        self._agent_to_user.put(message)

    def get_agent_message(self, timeout: float | None = None) -> Optional[ChatMessage]:
        return self._safe_get(self._agent_to_user, timeout)

    def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed

    @staticmethod
    def _safe_get(
        target_queue: queue.Queue[ChatMessage], timeout: float | None = None
    ) -> Optional[ChatMessage]:
        try:
            return target_queue.get(timeout=timeout)
        except queue.Empty:
            return None
