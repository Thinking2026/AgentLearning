from __future__ import annotations

from typing import Any

from schemas import AgentError, SQL_SCHEMA_TOOL_ERROR, ToolResult, build_error
from infra.db import RelationalStorage
from tools.tool_base import BaseTool, build_tool_output


def build_sql_schema_tool_name(backend_name: str) -> str:
    return f"inspect_{backend_name}_schema"


def build_sql_schema_tool_description(backend_name: str, resources: str = "") -> str:
    suffix = f" Available databases: {resources}." if resources else ""
    if backend_name == "sqlite":
        return (
            "Inspect the authorized SQLite database schema. "
            "Use this before querying when you are unsure which tables or columns exist."
            + suffix
        )
    if backend_name == "mysql":
        return (
            "Inspect the authorized MySQL database schema. "
            "Use this before querying when you are unsure which tables or columns exist."
            + suffix
        )
    return (
        f"Inspect the authorized schema exposed by the `{backend_name}` relational backend."
        + suffix
    )


class SQLSchemaTool(BaseTool):
    parameters = {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "Database alias or name to inspect. Optional when only one database is available.",
            },
            "table": {
                "type": "string",
                "description": (
                    "Table name to inspect. "
                    "Omit to list available tables; "
                    "provide to get column definitions (names, types, constraints)."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(
        self,
        name: str,
        description: str,
        storage: RelationalStorage,
        backend_name: str,
    ) -> None:
        self.name = name
        self.description = description
        self._storage = storage
        self._backend_name = backend_name

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        database = self._normalize_optional_string(arguments.get("database"))
        if isinstance(database, AgentError):
            return self._error_result(database)
        table = self._normalize_optional_string(arguments.get("table"))
        if isinstance(table, AgentError):
            return self._error_result(table)

        try:
            result = self._storage.inspect_schema(database=database, table=table)
        except AgentError as exc:
            return self._error_result(exc)
        except Exception as exc:
            error = build_error(SQL_SCHEMA_TOOL_ERROR, f"SQL schema tool failed unexpectedly: {exc}")
            return self._error_result(error)

        return ToolResult(
            output=build_tool_output(
                success=True,
                data=result,
            ),
            success=True,
        )

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None | AgentError:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized

    @staticmethod
    def _error_result(error: AgentError) -> ToolResult:
        return ToolResult(
            output=build_tool_output(success=False, error=error),
            success=False,
            error=error,
        )
