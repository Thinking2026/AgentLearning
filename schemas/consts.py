from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    NEW_TASK = "NEW_TASK"
    IN_PROGRESS = "IN_PROGRESS"
