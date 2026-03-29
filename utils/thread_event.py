from __future__ import annotations

import threading


class ThreadEvent:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._source: str | None = None

    def set(self, source: str | None = None) -> None:
        with self._lock:
            if not self._event.is_set():
                self._source = source or threading.current_thread().name
            self._event.set()

    def clear(self) -> None:
        with self._lock:
            self._source = None
            self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout=timeout)

    def get_source(self) -> str | None:
        with self._lock:
            return self._source

    def get_state(self) -> tuple[bool, str | None]:
        with self._lock:
            return self._event.is_set(), self._source
