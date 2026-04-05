from __future__ import annotations

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

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        like_query = f"%{query.strip()}%"
        sql = (
            f"SELECT id, title, content "
            f"FROM {self._quote_identifier(self._table_name)} "
            f"WHERE title LIKE %s OR content LIKE %s "
            f"LIMIT %s"
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (like_query, like_query, int(top_k)))
                rows = cursor.fetchall()
        return [
            {"id": row[0], "title": row[1], "content": row[2]}
            for row in rows
        ]

    def get_documents(self) -> list[dict]:
        sql = (
            f"SELECT id, title, content "
            f"FROM {self._quote_identifier(self._table_name)} "
            f"ORDER BY id"
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
        return [
            {"id": row[0], "title": row[1], "content": row[2]}
            for row in rows
        ]

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
