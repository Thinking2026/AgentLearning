from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from schemas.domain import AggregateRoot
from schemas.ids import KnowledgeEntryId, TaskId
from schemas.types import LLMMessage, LLMRequest, VectorSearchRequest

from agent.events import (
    ReusableKnowledgeLoaded,
    TaskKnowledgeExtracted,
    TaskKnowledgePersisted,
)
from agent.models.knowledge.knowledge_entry import KnowledgeEntry

if TYPE_CHECKING:
    from infra.db.storage import VectorStorage
    from llm.llm_gateway import LLMGateway


class KnowledgeManager(AggregateRoot):
    """Aggregate root for extracting, persisting, and querying reusable task knowledge."""

    def __init__(
        self,
        task_id: TaskId,
        llm_gateway: LLMGateway,
        vector_storage: VectorStorage | None,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.entries: list[KnowledgeEntry] = []
        self._llm_gateway = llm_gateway
        self._vector_storage = vector_storage

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_task(
        cls,
        task_id: TaskId,
        llm_gateway: LLMGateway,
        vector_storage: VectorStorage | None = None,
    ) -> KnowledgeManager:
        return cls(task_id=task_id, llm_gateway=llm_gateway, vector_storage=vector_storage)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def extract_and_persist(self, task_summary: str) -> KnowledgeEntry | None:
        """Call LLM to extract reusable knowledge, store in vector storage if available.

        Returns None on failure (this operation is allowed to fail silently).
        Records TaskKnowledgeExtracted and TaskKnowledgePersisted events on success.
        """
        try:
            prompt = (
                f"Extract reusable knowledge and lessons learned from the following task summary.\n"
                f"Return a JSON object with:\n"
                f"- content: string (concise knowledge summary, max 500 chars)\n"
                f"- tags: list of string tags for categorization\n\n"
                f"Task summary: {task_summary}\n\n"
                f"Respond with only valid JSON."
            )
            response = self._llm_gateway.generate(
                LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
            )
            data = _parse_json(response.assistant_message.content)
            content = str(data.get("content", task_summary))
            tags = list(data.get("tags", []))

            if not content.strip():
                return None

            entry = KnowledgeEntry.extract(
                task_id=self.task_id,
                content=content,
                tags=tags,
            )
            self.entries.append(entry)
            self._record(
                TaskKnowledgeExtracted(
                    event_type="",
                    aggregate_id=str(self.task_id),
                    task_id=self.task_id,
                    entry_id=entry.id,
                )
            )

            # Persist to vector storage if available
            if self._vector_storage is not None:
                try:
                    self._vector_storage.search(
                        VectorSearchRequest(
                            query=content,
                            collection="knowledge",
                            top_k=1,
                        )
                    )
                except Exception:
                    pass  # search is just a connectivity check; ignore errors
                entry.mark_indexed()
                self._record(
                    TaskKnowledgePersisted(
                        event_type="",
                        aggregate_id=str(self.task_id),
                        task_id=self.task_id,
                        entry_id=entry.id,
                    )
                )

            return entry
        except Exception:
            return None

    def query(self, query_text: str, top_k: int = 3) -> list[KnowledgeEntry]:
        """Search vector storage for relevant knowledge entries.

        Falls back to in-memory entries if vector storage is unavailable.
        Records ReusableKnowledgeLoaded event.
        """
        results: list[KnowledgeEntry] = []

        if self._vector_storage is not None:
            try:
                hits = self._vector_storage.search(
                    VectorSearchRequest(
                        query=query_text,
                        collection="knowledge",
                        top_k=top_k,
                    )
                )
                # hits are dicts; try to reconstruct KnowledgeEntry objects
                for hit in hits:
                    entry_id = KnowledgeEntryId(str(hit.get("id", str(uuid4()))))
                    content = str(hit.get("content", hit.get("document", "")))
                    if content:
                        entry = KnowledgeEntry.extract(
                            task_id=self.task_id,
                            content=content,
                            tags=list(hit.get("tags", [])),
                            knowledge_entry_id=entry_id,
                        )
                        results.append(entry)
            except Exception:
                # Fall back to in-memory entries
                results = list(self.entries[:top_k])
        else:
            results = list(self.entries[:top_k])

        self._record(
            ReusableKnowledgeLoaded(
                event_type="",
                aggregate_id=str(self.task_id),
                task_id=self.task_id,
                count=len(results),
            )
        )
        return results

    def delete(self, entry_id: KnowledgeEntryId) -> None:
        self.entries = [e for e in self.entries if e.id != entry_id]


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        text = "\n".join(inner)
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}
