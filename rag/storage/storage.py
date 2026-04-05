from __future__ import annotations

from abc import ABC
from typing import Any


class BaseStorage(ABC):
    backend_name: str = "base"

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support document-style search."
        )

    def get_documents(self) -> list[dict]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support document export."
        )

    def query(
        self,
        statement: str,
        params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None,
        max_rows: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support generic SQL queries."
        )

    def close(self) -> None:
        return None
