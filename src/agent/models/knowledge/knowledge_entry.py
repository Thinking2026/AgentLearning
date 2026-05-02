from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from schemas.domain import AggregateRoot, DomainEvent
from schemas.ids import KnowledgeEntryId, TaskId
from schemas.task import KnowledgeEntryStatus, KnowledgeExtracted, KnowledgeIndexed


class DomainRuleViolation(Exception):
    """Raised when a domain invariant is violated."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _event(event_type: type[DomainEvent], **kwargs: Any) -> DomainEvent:
    return event_type(event_type="", aggregate_id="", **kwargs)


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
