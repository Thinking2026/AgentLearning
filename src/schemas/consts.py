from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    NEW_TASK = "NEW_TASK"
    IN_PROGRESS = "IN_PROGRESS"
