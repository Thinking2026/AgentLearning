from __future__ import annotations

from abc import ABC
from typing import Any

from schemas import (
    KeyValueGetRequest,
    KeyValueSetRequest,
    SQLQueryRequest,
    VectorSearchRequest,
)


class BaseStorage(ABC):
    backend_name: str = "base"

    def capabilities(self) -> set[str]:
        return set()

    def list_resources(self) -> list[str]:
        return []

    def describe_schema(self) -> dict[str, Any]:
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "resources": self.list_resources(),
        }

    def close(self) -> None:
        return None


class RelationalStorage(BaseStorage):
    def query(self, request: SQLQueryRequest) -> list[dict[str, Any]]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support relational queries."
        )

    def inspect_schema(
        self,
        *,
        database: str | None = None,
        table: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support schema inspection."
        )


class VectorStorage(BaseStorage):
    def search(self, request: VectorSearchRequest) -> list[dict[str, Any]]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support vector search."
        )

    def inspect_schema(self, collection: str | None = None) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support vector schema inspection."
        )


class KeyValueStorage(BaseStorage):
    def get(self, request: KeyValueGetRequest) -> dict[str, Any] | None:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support key-value reads."
        )

    def set(self, request: KeyValueSetRequest) -> None:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support key-value writes."
        )

    def delete(self, key: str) -> bool:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support key-value deletes."
        )


class DocumentStorage(BaseStorage):
    def get_documents(self) -> list[dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support document export."
        )
