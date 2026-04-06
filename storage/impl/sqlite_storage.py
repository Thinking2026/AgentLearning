from __future__ import annotations

import sqlite3
from pathlib import Path

from schemas import build_error
from storage.contracts import SQLQueryRequest
from storage.storage import RelationalStorage


class SQLiteStorage(RelationalStorage):
    backend_name = "sqlite"

    def __init__(self, database_path: str) -> None:
        self._database_path = Path(database_path)
        self._initialize()

    def capabilities(self) -> set[str]:
        return {"sql_query", "document_seed"}

    def describe_schema(self) -> dict[str, object]:
        tables = self.query(
            SQLQueryRequest(
                statement=(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = ? AND name NOT LIKE ? "
                    "ORDER BY name"
                ),
                params=("table", "sqlite_%"),
                max_rows=200,
            )
        )
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "database_path": str(self._database_path),
            "tables": tables,
        }

    def query(self, request: SQLQueryRequest) -> list[dict[str, object]]:
        normalized_statement = self._validate_select_statement(request.statement)
        row_limit = self._normalize_max_rows(request.max_rows)
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized_statement, request.params or ())
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
