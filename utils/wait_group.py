from __future__ import annotations

import time
from threading import Condition, Lock


class WaitGroup:
    def __init__(self) -> None:
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._count = 0

    def add(self, delta: int = 1) -> None:
        if delta == 0:
            return
        with self._condition:
            next_count = self._count + delta
            if next_count < 0:
                raise ValueError("wait group counter cannot be negative")
            self._count = next_count
            if self._count == 0:
                self._condition.notify_all()

    def done(self) -> None:
        self.add(-1)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._count > 0:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    @property
    def count(self) -> int:
        with self._condition:
            return self._count
