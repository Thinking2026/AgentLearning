from __future__ import annotations

from rag.storage import BaseStorage
from schemas import AgentError, build_error
from tracing import Span, Tracer


class RAGService:
    def __init__(self, storage: BaseStorage, tracer: Tracer | None = None) -> None:
        self._storage = storage
        self._tracer = tracer

    def use_storage(self, storage: BaseStorage) -> None:
        self._storage = storage

    def set_tracer(self, tracer: Tracer | None) -> None:
        self._tracer = tracer

    def get_source_name(self) -> str:
        return self._storage.backend_name

    def _start_span(
        self,
        name: str,
        attributes: dict | None = None,
    ) -> Span:
        if self._tracer is None:
            return Span(None)
        return self._tracer.start_span(name=name, type="rag", attributes=attributes)

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        with self._start_span(
            "rag.retrieve",
            attributes={
                "query": query,
                "top_k": top_k,
                "storage_backend": self._storage.backend_name,
            },
        ) as span:
            matches = self._retrieve_matches(query, top_k=top_k)
            span.add_attributes({"match_count": len(matches)})
            return [
                {
                    "source_id": item["id"],
                    "title": item["title"],
                    "content": item["content"],
                }
                for item in matches
            ]

    def _retrieve_matches(self, query: str, top_k: int = 3) -> list[dict]:
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
        return matches
