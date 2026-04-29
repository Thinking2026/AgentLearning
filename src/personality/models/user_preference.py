from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import TaskId, UserId
from task.models.entities import DomainRuleViolation, TaskStatus


def _event(event_type: type[DomainEvent], **kwargs: Any) -> DomainEvent:
    return event_type(event_type="", aggregate_id="", **kwargs)


@dataclass
class UserPreferenceSubmitted(DomainEvent):
    user_id: UserId = field(default="")
    key: str = field(default="")
    value: str = field(default="")
    task_id: TaskId | None = field(default=None)

    def __post_init__(self) -> None:
        self.event_type = "UserPreferenceSubmitted"
        self.aggregate_id = self.user_id


@dataclass
class UserPreferenceSaved(DomainEvent):
    user_id: UserId = field(default="")
    key: str = field(default="")
    value: str = field(default="")
    task_id: TaskId | None = field(default=None)

    def __post_init__(self) -> None:
        self.event_type = "UserPreferenceSaved"
        self.aggregate_id = self.user_id


@dataclass
class UserPreferenceUpdated(DomainEvent):
    user_id: UserId = field(default="")
    key: str = field(default="")
    value: str = field(default="")
    task_id: TaskId | None = field(default=None)

    def __post_init__(self) -> None:
        self.event_type = "UserPreferenceUpdated"
        self.aggregate_id = self.user_id


@dataclass
class UserPreference(AggregateRoot):
    """User Preference aggregate with atomic submit/save semantics."""

    user_id: UserId
    values: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def apply(
        cls,
        user_id: UserId,
        key: str,
        value: str,
        *,
        task_id: TaskId | None = None,
        task_status: TaskStatus | None = None,
        existing: UserPreference | None = None,
    ) -> UserPreference:
        preference = existing or cls(user_id=user_id)
        preference.apply_value(key, value, task_id=task_id, task_status=task_status)
        return preference

    def apply_value(
        self,
        key: str,
        value: str,
        *,
        task_id: TaskId | None = None,
        task_status: TaskStatus | None = None,
    ) -> None:
        if not key.strip():
            raise DomainRuleViolation("preference key must not be empty")
        if task_status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED, TaskStatus.TERMINATED, TaskStatus.DELIVERED}:
            raise DomainRuleViolation("preference changes do not affect terminal tasks")
        self.values[key] = value
        self._record(_event(UserPreferenceSubmitted, user_id=self.user_id, key=key, value=value, task_id=task_id))
        self._record(_event(UserPreferenceSaved, user_id=self.user_id, key=key, value=value, task_id=task_id))
        self._record(_event(UserPreferenceUpdated, user_id=self.user_id, key=key, value=value, task_id=task_id))

    def snapshot(self) -> dict[str, str]:
        return dict(self.values)
