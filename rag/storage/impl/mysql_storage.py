from __future__ import annotations

from typing import Any

from rag.storage.storage import BaseStorage
from schemas import build_error


class MySQLStorage(BaseStorage):
    backend_name = "mysql"

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table_name: str = "documents",
        charset: str = "utf8mb4",
    ) -> None:
        if not host.strip():
            raise build_error("STORAGE_CONFIG_ERROR", "MySQL storage requires a non-empty host.")
        if not user.strip():
            raise build_error("STORAGE_CONFIG_ERROR", "MySQL storage requires a non-empty user.")
        if not database.strip():
            raise build_error("STORAGE_CONFIG_ERROR", "MySQL storage requires a non-empty database.")
        if not table_name.strip():
            raise build_error("STORAGE_CONFIG_ERROR", "MySQL storage requires a non-empty table_name.")

        self._host = host
        self._port = int(port)
        self._user = user
        self._password = password
        self._database = database
        self._table_name = table_name
        self._charset = charset
        self._pymysql = self._require_pymysql()

    def query(
        self,
        statement: str,
        params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None,
        max_rows: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_statement = self._validate_select_statement(statement)
        row_limit = self._normalize_max_rows(max_rows)
        with self._connect() as connection:
            with connection.cursor(self._pymysql.cursors.DictCursor) as cursor:
                cursor.execute(normalized_statement, params or ())
                rows = cursor.fetchmany(row_limit)
        return list(rows)

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

    def _connect(self):
        return self._pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=self._database,
            charset=self._charset,
        )

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        escaped = identifier.replace("`", "``")
        return f"`{escaped}`"

    @staticmethod
    def _require_pymysql():
        try:
            import pymysql
        except ModuleNotFoundError as exc:
            raise build_error(
                "STORAGE_DEPENDENCY_ERROR",
                "MySQL storage requires the `pymysql` package to be installed.",
            ) from exc
        return pymysql
