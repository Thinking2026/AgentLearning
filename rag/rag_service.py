from __future__ import annotations

from rag.storage import BaseStorage
from schemas import AgentError, build_error


class RAGService:
    def __init__(self, storage: BaseStorage) -> None:
        self._storage = storage

    def use_storage(self, storage: BaseStorage) -> None:
        self._storage = storage

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        try:
            matches = self._storage.search(query, top_k=top_k)
        except TimeoutError as exc:
            raise build_error("RAG_TIMEOUT", f"RAG external data source timed out: {exc}") from exc
        except AgentError as exc:
            if "TIMEOUT" in exc.code:
                raise build_error("RAG_TIMEOUT", f"RAG external data source timed out: {exc.message}") from exc
            raise build_error("RAG_EXTERNAL_ERROR", f"RAG external data source error: {exc.message}") from exc
        except Exception as exc:
            raise build_error("RAG_EXTERNAL_ERROR", f"RAG external data source error: {exc}") from exc
        return [
            {
                "source_id": item["id"],
                "title": item["title"],
                "content": item["content"],
            }
            for item in matches
        ]
