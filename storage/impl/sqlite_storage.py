from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from schemas import build_error
from storage.storage import BaseStorage


class SQLiteStorage(BaseStorage):
    backend_name = "sqlite"

    def __init__(self, database_path: str) -> None:
        self._database_path = Path(database_path)
        self._initialize()

    def query(
        self,
        statement: str,
        params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None,
        max_rows: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_statement = self._validate_select_statement(statement)
        row_limit = self._normalize_max_rows(max_rows)
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized_statement, params or ())
            rows = cursor.fetchmany(row_limit)
        return [dict(row) for row in rows]

    def seed(self, documents: list[dict]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO documents(id, title, content)
                VALUES (?, ?, ?)
                """,
                [(doc["id"], doc["title"], doc["content"]) for doc in documents],
            )
            connection.commit()

    @staticmethod
    def _validate_select_statement(statement: str) -> str:
        normalized = statement.strip()
        if not normalized:
            raise build_error("STORAGE_QUERY_ERROR", "SQL query must not be empty.")
        compact = normalized.rstrip().rstrip(";").strip()
        if ";" in compact:
            raise build_error("STORAGE_QUERY_ERROR", "Only a single SQL statement is allowed.")
        if not compact.lower().startswith("select"):
            raise build_error("STORAGE_QUERY_ERROR", "Only SELECT queries are allowed.")
        return compact

    @staticmethod
    def _normalize_max_rows(max_rows: int) -> int:
        try:
            normalized = int(max_rows)
        except (TypeError, ValueError):
            normalized = 100
        return max(1, min(normalized, 1000))

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL
                )
                """
            )
            connection.commit()
