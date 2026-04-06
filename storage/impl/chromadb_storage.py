from __future__ import annotations

from schemas import VectorSearchRequest, build_error
from storage.storage import DocumentStorage, VectorStorage


class ChromaDBStorage(VectorStorage, DocumentStorage):
    backend_name = "chromadb"

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "agent_documents",
    ) -> None:
        try:
            import chromadb
        except ModuleNotFoundError as exc:
            raise build_error(
                "STORAGE_DEPENDENCY_ERROR",
                "ChromaDB storage requires the `chromadb` package to be installed.",
            ) from exc

        self._client = chromadb.PersistentClient(path=persist_directory)
        self._collection = self._client.get_or_create_collection(name=collection_name)

    def capabilities(self) -> set[str]:
        return {"vector_search", "document_list", "document_upsert"}

    def describe_schema(self) -> dict[str, object]:
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "collection_name": self._collection.name,
        }

    def search(self, request: VectorSearchRequest) -> list[dict]:
        result = self._collection.query(
            query_texts=[request.query],
            n_results=request.top_k,
            where=request.filters or None,
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {
                "id": ids[index],
                "score": None if index >= len(distances) else distances[index],
                "title": (metadatas[index] or {}).get("title", ids[index]),
                "content": documents[index],
                "metadata": metadatas[index] or {},
            }
            for index in range(len(ids))
        ]

    def get_documents(self) -> list[dict]:
        result = self._collection.get(include=["documents", "metadatas"])
        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        return [
            {
                "id": ids[index],
                "title": (metadatas[index] or {}).get("title", ids[index]),
                "content": documents[index],
            }
            for index in range(len(ids))
        ]

    def upsert_documents(self, documents: list[dict]) -> None:
        self._collection.upsert(
            ids=[doc["id"] for doc in documents],
            documents=[doc["content"] for doc in documents],
            metadatas=[{"title": doc.get("title", doc["id"])} for doc in documents],
        )
