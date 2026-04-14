from __future__ import annotations

from schemas import VectorSearchRequest, build_error
from storage.storage import DocumentStorage, VectorStorage


class ChromaDBStorage(VectorStorage, DocumentStorage):
    backend_name = "chromadb"

    def __init__(
        self,
        persist_directory: str,
        collections: list[str],
    ) -> None:
        try:
            import chromadb
        except ModuleNotFoundError as exc:
            raise build_error(
                "STORAGE_DEPENDENCY_ERROR",
                "ChromaDB storage requires the `chromadb` package to be installed.",
            ) from exc

        normalized_collections = [collection.strip() for collection in collections if str(collection).strip()]
        if not normalized_collections:
            raise build_error("STORAGE_CONFIG_ERROR", "ChromaDB storage requires at least one collection.")
        self._client = chromadb.PersistentClient(path=persist_directory)
        self._collections = {
            name: self._client.get_or_create_collection(name=name)
            for name in normalized_collections
        }

    def capabilities(self) -> set[str]:
        return {"vector_search", "document_list", "document_upsert"}

    def list_resources(self) -> list[str]:
        return sorted(self._collections)

    def describe_schema(self) -> dict[str, object]:
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "collections": sorted(self._collections),
        }

    def inspect_schema(self, collection: str | None = None) -> dict[str, object]:
        if collection is None or not str(collection).strip():
            return {
                "backend": self.backend_name,
                "collection": None,
                "collections": sorted(self._collections),
            }

        normalized = str(collection).strip()
        resolved_collection = self._resolve_collection(normalized)
        result = resolved_collection.get(limit=3, include=["documents", "metadatas"])
        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        examples = []
        for index in range(len(ids)):
            examples.append(
                {
                    "id": ids[index],
                    "title": (metadatas[index] or {}).get("title", ids[index]),
                    "content_preview": str(documents[index])[:200],
                    "metadata": metadatas[index] or {},
                }
            )
        total_count = resolved_collection.count()
        return {
            "backend": self.backend_name,
            "collection": normalized,
            "document_count": total_count,
            "example_documents": examples,
        }

    def search(self, request: VectorSearchRequest) -> list[dict]:
        collection = self._resolve_collection(request.collection)
        result = collection.query(
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

    def get_documents(self, collection_name: str | None = None) -> list[dict]:
        collection = self._resolve_collection(collection_name)
        result = collection.get(include=["documents", "metadatas"])
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

    def upsert_documents(self, collection_name: str, documents: list[dict]) -> None:
        collection = self._resolve_collection(collection_name)
        collection.upsert(
            ids=[doc["id"] for doc in documents],
            documents=[doc["content"] for doc in documents],
            metadatas=[{"title": doc.get("title", doc["id"])} for doc in documents],
        )

    def _resolve_collection(self, collection_name: str | None) -> object:
        if collection_name is not None and str(collection_name).strip():
            normalized = str(collection_name).strip()
            if normalized in self._collections:
                return self._collections[normalized]
            available = ", ".join(sorted(self._collections)) or "<none>"
            raise build_error(
                "STORAGE_RESOURCE_NOT_FOUND",
                f"Unknown ChromaDB collection `{collection_name}`. Available collections: {available}",
            )
        if len(self._collections) == 1:
            return next(iter(self._collections.values()))
        available = ", ".join(sorted(self._collections)) or "<none>"
        raise build_error(
            "STORAGE_RESOURCE_REQUIRED",
            f"ChromaDB collection is required. Available collections: {available}",
        )
