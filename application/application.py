from __future__ import annotations

from pathlib import Path

from config import JsonConfig, load_config
from context.shared_context import SharedContext
from queue.message_queue import MessageQueue

from .agent_thread import AgentThread
from .user_thread import UserThread


class AgentApplication:
    def __init__(self, config: JsonConfig) -> None:
        self._config = config
        self._message_queue = MessageQueue()
        self._shared_context = SharedContext()
        self._agent_thread = AgentThread(
            message_queue=self._message_queue,
            shared_context=self._shared_context,
            config=self._config,
        )
        self._user_thread = UserThread(
            message_queue=self._message_queue,
            shared_context=self._shared_context,
        )

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "AgentApplication":
        return cls(load_config(config_path))

    def run(self) -> None:
        self._agent_thread.start()
        self._user_thread.start()

        try:
            self._user_thread.join()
            self._agent_thread.join(timeout=1)
        except KeyboardInterrupt:
            self._message_queue.close()
            self._user_thread.stop()
        finally:
            if self._agent_thread.is_alive():
                self._agent_thread.stop()
                self._agent_thread.join(timeout=1)
