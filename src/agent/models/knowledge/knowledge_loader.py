from __future__ import annotations

from typing import TYPE_CHECKING

from agent.models.knowledge.knowledge_entry import KnowledgeEntry

if TYPE_CHECKING:
    from agent.models.knowledge.knowledge_manager import KnowledgeManager


class KnowledgeLoader:
    """Entity that loads reusable knowledge for a given query.

    Wraps KnowledgeManager and provides a simple load() interface.
    If no KnowledgeManager is injected, returns an empty list.
    """

    def __init__(self, knowledge_manager: KnowledgeManager | None = None) -> None:
        self._knowledge_manager = knowledge_manager

    def load(self, query_text: str, top_k: int = 3) -> list[KnowledgeEntry]:
        """Query the knowledge manager for relevant entries.

        Returns an empty list if no knowledge manager is available.
        """
        if self._knowledge_manager is None:
            return []
        try:
            return self._knowledge_manager.query(query_text, top_k=top_k)
        except Exception:
            return []
