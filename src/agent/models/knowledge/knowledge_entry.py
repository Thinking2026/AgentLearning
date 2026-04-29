from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import KnowledgeEntryId, TaskId
from agent.models.task.task_entities import DomainRuleViolation


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _event(event_type: type[DomainEvent], **kwargs: Any) -> DomainEvent:
    return event_type(event_type="", aggregate_id="", **kwargs)


class KnowledgeEntryStatus(str, Enum):
    EXTRACTED = "Extracted"
    INDEXED = "Indexed"


@dataclass
class KnowledgeExtracted(DomainEvent):
    knowledge_entry_id: KnowledgeEntryId = field(default="")
    task_id: TaskId = field(default="")
    content: str = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "KnowledgeExtracted"
        self.aggregate_id = self.knowledge_entry_id


@dataclass
class KnowledgeIndexed(DomainEvent):
    knowledge_entry_id: KnowledgeEntryId = field(default="")
    task_id: TaskId = field(default="")

    def __post_init__(self) -> None:
        self.event_type = "KnowledgeIndexed"
        self.aggregate_id = self.knowledge_entry_id


@dataclass
class KnowledgeEntry(AggregateRoot):
    """Knowledge Flywheel aggregate for reusable task knowledge."""

    id: KnowledgeEntryId
    task_id: TaskId
    content: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    status: KnowledgeEntryStatus = KnowledgeEntryStatus.EXTRACTED

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self)

    @classmethod
    def extract(
        cls,
        task_id: TaskId,
        content: str,
        tags: list[str] | tuple[str, ...] = (),
        knowledge_entry_id: KnowledgeEntryId | None = None,
    ) -> KnowledgeEntry:
        if not content.strip():
            raise DomainRuleViolation("knowledge content must not be empty")
        entry = cls(
            id=knowledge_entry_id or KnowledgeEntryId(_new_id("knowledge")),
            task_id=task_id,
            content=content,
            tags=tuple(tags),
        )
        entry._record(_event(KnowledgeExtracted, knowledge_entry_id=entry.id, task_id=task_id, content=content))
        return entry

    def mark_indexed(self) -> None:
        if self.status != KnowledgeEntryStatus.EXTRACTED:
            raise DomainRuleViolation("only extracted knowledge can be indexed")
        self.status = KnowledgeEntryStatus.INDEXED
        self._record(_event(KnowledgeIndexed, knowledge_entry_id=self.id, task_id=self.task_id))
