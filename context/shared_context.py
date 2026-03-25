from __future__ import annotations

import threading


class SharedContext:
    def __init__(self) -> None:
        self._system_prompt = ""
        self._lock = threading.RLock()

    def get_system_prompt(self) -> str:
        with self._lock:
            return self._system_prompt

    def append_system_prompt(self, text: str) -> None:
        with self._lock:
            self._system_prompt += text

    def append_system_prompt_line(self, text: str) -> None:
        with self._lock:
            self._system_prompt += f"\n{text}"
