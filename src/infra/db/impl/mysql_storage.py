from __future__ import annotations

from schemas import (
    SQLQueryRequest,
    STORAGE_CONFIG_ERROR,
    STORAGE_DEPENDENCY_ERROR,
    STORAGE_QUERY_ERROR,
    STORAGE_RESOURCE_NOT_FOUND,
    STORAGE_RESOURCE_REQUIRED,
    build_pipeline_error,
)
from infra.db.storage import RelationalStorage


class MySQLStorage(RelationalStorage):
    backend_name = "mysql"

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        allowed_databases: list[str],
        charset: str = "utf8mb4",
    ) -> None:
        if not host.strip():
            raise build_pipeline_error(STORAGE_CONFIG_ERROR, "MySQL storage requires a non-empty host.")
        if not user.strip():
            raise build_pipeline_error(STORAGE_CONFIG_ERROR, "MySQL storage requires a non-empty user.")
        normalized_databases = [database.strip() for database in allowed_databases if str(database).strip()]
        if not normalized_databases:
            raise build_pipeline_error(STORAGE_CONFIG_ERROR, "MySQL storage requires at least one allowed database.")

        self._host = host
        self._port = int(port)
        self._user = user
        self._password = password
        self._allowed_databases = normalized_databases
        self._charset = charset
        self._pymysql = self._require_pymysql()

    def capabilities(self) -> set[str]:
        return {"sql_query"}

    def list_resources(self) -> list[str]:
        return sorted(self._allowed_databases)

    def describe_schema(self) -> dict[str, object]:
        return {
            "backend_name": self.backend_name,
            "capabilities": sorted(self.capabilities()),
            "databases": sorted(self._allowed_databases),
        }

    def inspect_schema(
        self,
        *,
        database: str | None = None,
        table: str | None = None,
    ) -> dict[str, object]:
        database_name = self._resolve_database_name(database)
        with self._connect(database_name) as connection:
            with connection.cursor(self._pymysql.cursors.DictCursor) as cursor:
                if table is None or not str(table).strip():
                    cursor.execute(
                        """
                        SELECT table_name, table_type
                        FROM information_schema.tables
                        WHERE table_schema = %s
                        ORDER BY table_name
                        """,
                        (database_name,),
                    )
                    return {
                        "backend": self.backend_name,
                        "database": database_name,
                        "table": None,
                        "tables": list(cursor.fetchall()),
                    }

                table_name = str(table).strip()
                cursor.execute(
                    """
                    SELECT table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    LIMIT 1
                    """,
                    (database_name, table_name),
                )
                table_info = cursor.fetchone()
                if table_info is None:
                    cursor.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = %s
                        ORDER BY table_name
                        """,
                        (database_name,),
                    )
                    available = ", ".join(row["table_name"] for row in cursor.fetchall()) or "<none>"
                    raise build_pipeline_error(
                        STORAGE_RESOURCE_NOT_FOUND,
                        f"Unknown MySQL table `{table_name}` in database `{database_name}`. "
                        f"Available tables: {available}",
                    )
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_key, column_default, extra
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (database_name, table_name),
                )
                return {
                    "backend": self.backend_name,
                    "database": database_name,
                    "table": table_name,
                    "table_info": table_info,
                    "columns": list(cursor.fetchall()),
                }

    def query(self, request: SQLQueryRequest) -> list[dict[str, object]]:
        database_name = self._resolve_database_name(request.database)
        normalized_statement = self._validate_select_statement(request.statement)
        row_limit = self._normalize_max_rows(request.max_rows)
        with self._connect(database_name) as connection:
            with connection.cursor(self._pymysql.cursors.DictCursor) as cursor:
                cursor.execute(normalized_statement, request.params or ())
                rows = cursor.fetchmany(row_limit)
        return list(rows)

    def _connect(self, database_name: str):
        return self._pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=database_name,
            charset=self._charset,
        )

    def _resolve_database_name(self, database_name: str | None) -> str:
        if database_name is not None and str(database_name).strip():
            normalized = str(database_name).strip()
            if normalized in self._allowed_databases:
                return normalized
            available = ", ".join(sorted(self._allowed_databases)) or "<none>"
            raise build_pipeline_error(
                STORAGE_RESOURCE_NOT_FOUND,
                f"Unknown MySQL database `{database_name}`. Available databases: {available}",
            )
        if len(self._allowed_databases) == 1:
            return self._allowed_databases[0]
        available = ", ".join(sorted(self._allowed_databases)) or "<none>"
        raise build_pipeline_error(
            STORAGE_RESOURCE_REQUIRED,
            f"MySQL database is required. Available databases: {available}",
        )

    @staticmethod
    def _validate_select_statement(statement: str) -> str:
        normalized = statement.strip()
        if not normalized:
            raise build_pipeline_error(STORAGE_QUERY_ERROR, "SQL query must not be empty.")
        compact = normalized.rstrip().rstrip(";").strip()
        if ";" in compact:
            raise build_pipeline_error(STORAGE_QUERY_ERROR, "Only a single SQL statement is allowed.")
        if not compact.lower().startswith("select"):
            raise build_pipeline_error(STORAGE_QUERY_ERROR, "Only SELECT queries are allowed.")
        return compact

    @staticmethod
    def _normalize_max_rows(max_rows: int) -> int:
        try:
            normalized = int(max_rows)
        except (TypeError, ValueError):
            normalized = 100
        return max(1, min(normalized, 1000))

    @staticmethod
    def _require_pymysql():
        try:
            import pymysql
        except ModuleNotFoundError as exc:
            raise build_pipeline_error(
                STORAGE_DEPENDENCY_ERROR,
                "MySQL storage requires the `pymysql` package to be installed.",
            ) from exc
        return pymysql
