from __future__ import annotations

import sqlite3
from pathlib import Path

from schemas import (
    SQLQueryRequest,
    STORAGE_CONFIG_ERROR,
    STORAGE_QUERY_ERROR,
    STORAGE_RESOURCE_NOT_FOUND,
    STORAGE_RESOURCE_REQUIRED,
    build_error,
)
from infra.db.storage import RelationalStorage


class SQLiteStorage(RelationalStorage):
    backend_name = "sqlite"
    _READ_ONLY_PRAGMA_PREFIXES = (
        "pragma table_info(",
        "pragma table_xinfo(",
        "pragma index_list(",
        "pragma index_info(",
        "pragma index_xinfo(",
        "pragma foreign_key_list(",
    )

    def __init__(self, databases: dict[str, str]) -> None:
        if not databases:
            raise build_error(STORAGE_CONFIG_ERROR, "SQLite storage requires at least one database.")
        self._databases = {
            self._normalize_database_name(name): Path(path).expanduser()
            for name, path in databases.items()
            if str(name).strip() and str(path).strip()
        }
        if not self._databases:
            raise build_error(STORAGE_CONFIG_ERROR, "SQLite storage requires valid database mappings.")
        for database_path in self._databases.values():
            database_path.parent.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> set[str]:
        return {"sql_query"}

    def list_resources(self) -> list[str]:
        return sorted(self._databases)

    def describe_schema(self) -> dict[str, object]:
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "databases": {
                name: str(path)
                for name, path in sorted(self._databases.items())
            },
        }

    def inspect_schema(
        self,
        *,
        database: str | None = None,
        table: str | None = None,
    ) -> dict[str, object]:
        database_name = self._resolve_database_name(database)
        database_path = self._databases[database_name]
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            if table is None or not str(table).strip():
                rows = connection.execute(
                    """
                    SELECT name, type, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                ).fetchall()
                return {
                    "backend": self.backend_name,
                    "database": database_name,
                    "table": None,
                    "tables": [dict(row) for row in rows],
                }

            table_name = str(table).strip()
            table_rows = connection.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE name = ? LIMIT 1",
                (table_name,),
            ).fetchall()
            if not table_rows:
                available = ", ".join(self._list_table_names(connection)) or "<none>"
                raise build_error(
                    STORAGE_RESOURCE_NOT_FOUND,
                    f"Unknown SQLite table `{table_name}` in database `{database_name}`. "
                    f"Available tables: {available}",
                )
            column_rows = connection.execute(
                f"PRAGMA table_info({self._quote_identifier(table_name)})"
            ).fetchall()
            return {
                "backend": self.backend_name,
                "database": database_name,
                "table": table_name,
                "table_info": dict(table_rows[0]),
                "columns": [dict(row) for row in column_rows],
            }

    def query(self, request: SQLQueryRequest) -> list[dict[str, object]]:
        database_name = self._resolve_database_name(request.database)
        database_path = self._databases[database_name]
        normalized_statement = self._validate_select_statement(request.statement)
        row_limit = self._normalize_max_rows(request.max_rows)
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized_statement, request.params or ())
            rows = cursor.fetchmany(row_limit)
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_select_statement(statement: str) -> str:
        normalized = statement.strip()
        if not normalized:
            raise build_error(STORAGE_QUERY_ERROR, "SQL query must not be empty.")
        compact = normalized.rstrip().rstrip(";").strip()
        if ";" in compact:
            raise build_error(STORAGE_QUERY_ERROR, "Only a single SQL statement is allowed.")
        lower_compact = compact.lower()
        if lower_compact.startswith("select"):
            return compact
        if lower_compact.startswith(SQLiteStorage._READ_ONLY_PRAGMA_PREFIXES):
            return compact
        raise build_error(
            STORAGE_QUERY_ERROR,
            "Only read-only SELECT queries and safe schema PRAGMA queries are allowed.",
        )

    @staticmethod
    def _normalize_max_rows(max_rows: int) -> int:
        try:
            normalized = int(max_rows)
        except (TypeError, ValueError):
            normalized = 100
        return max(1, min(normalized, 1000))

    def _resolve_database_name(self, database_name: str | None) -> str:
        if database_name is not None and str(database_name).strip():
            normalized = self._normalize_database_name(database_name)
            if normalized in self._databases:
                return normalized
            available = ", ".join(sorted(self._databases)) or "<none>"
            raise build_error(
                STORAGE_RESOURCE_NOT_FOUND,
                f"Unknown SQLite database `{database_name}`. Available databases: {available}",
            )
        if len(self._databases) == 1:
            return next(iter(self._databases))
        available = ", ".join(sorted(self._databases)) or "<none>"
        raise build_error(
            STORAGE_RESOURCE_REQUIRED,
            f"SQLite database is required. Available databases: {available}",
        )

    @staticmethod
    def _normalize_database_name(value: str) -> str:
        normalized = str(value).strip()
        if normalized.endswith(".db"):
            normalized = normalized[:-3]
        return normalized

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @staticmethod
    def _list_table_names(connection: sqlite3.Connection) -> list[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [str(row[0]) for row in rows]
