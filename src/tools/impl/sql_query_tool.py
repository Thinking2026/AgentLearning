from __future__ import annotations

from typing import Any

from schemas import AGENT_EXECUTION_ERROR, AgentError, SQL_QUERY_TOOL_ERROR, TOOL_ARGUMENT_ERROR, ToolResult, build_error
from infra.db import RelationalStorage, SQLQueryRequest
from tools.tool_base import BaseTool, build_tool_output


def build_sql_query_tool_name(backend_name: str) -> str:
    return f"query_{backend_name}_data"


def build_sql_query_tool_description(backend_name: str, resources: str = "") -> str:
    suffix = f" Available databases: {resources}." if resources else ""
    if backend_name == "sqlite":
        return (
            "Run a single read-only SELECT query against the SQLite database. "
            "Use for relational tables with custom schemas; choose the correct authorized database for the task. "
            "Send only a single SELECT statement and keep values in params instead of string interpolation."
            + suffix
        )
    if backend_name == "mysql":
        return (
            "Run a single read-only SELECT query against the MySQL database. "
            "Use for relational tables with custom schemas; choose the correct authorized database for the task. "
            "Send only a single SELECT statement and keep values in params instead of string interpolation."
            + suffix
        )
    return (
        f"Run a single read-only SELECT query against the `{backend_name}` relational backend. "
        "Use this when the answer depends on structured table data."
        + suffix
    )


class SQLQueryTool(BaseTool):
    parameters = {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "Database alias or name to query. Optional when only one database is available.",
            },
            "statement": {
                "type": "string",
                "description": (
                    "A single SELECT statement. "
                    "Non-SELECT statements may be rejected by the backend. "
                    "Use placeholders (? or :name) instead of interpolating values directly."
                ),
            },
            "params": {
                "description": (
                    "Parameters bound to placeholders in the statement. "
                    "Array for positional placeholders (?), object for named placeholders (:name). "
                    "Omit when the statement has no placeholders."
                ),
                "oneOf": [
                    {"type": "array", "items": {}},
                    {"type": "object"},
                ],
            },
            "max_rows": {
                "type": "integer",
                "description": "Maximum number of rows to return. Defaults to 50, must be between 1 and 1000.",
                "default": 50,
                "minimum": 1,
                "maximum": 1000,
            },
        },
        "required": ["statement"],
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
        database = self._normalize_database(arguments.get("database"))
        if isinstance(database, AgentError):
            return self._error_result(database)
        statement = str(arguments.get("statement", "")).strip()
        if not statement:
            error = build_error(TOOL_ARGUMENT_ERROR, "SQL query tool requires a non-empty statement.")
            return self._error_result(error)

        params = arguments.get("params")
        normalized_params = self._normalize_params(params)
        if isinstance(normalized_params, AgentError):
            return self._error_result(normalized_params)

        max_rows = self._normalize_max_rows(arguments.get("max_rows", 50))
        if isinstance(max_rows, AgentError):
            return self._error_result(max_rows)

        try:
            rows = self._storage.query(
                SQLQueryRequest(
                    database=database,
                    statement=statement,
                    params=normalized_params,
                    max_rows=max_rows,
                )
            )
        except AgentError as exc:
            return self._error_result(exc)
        except Exception as exc:
            error = build_error(SQL_QUERY_TOOL_ERROR, f"SQL query tool failed unexpectedly: {exc}")
            return self._error_result(error)

        columns = list(rows[0].keys()) if rows else []
        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "backend": self._backend_name,
                    "database": database,
                    "statement": statement,
                    "row_count": len(rows),
                    "columns": columns,
                    "rows": rows,
                },
            ),
            success=True,
        )

    @staticmethod
    def _normalize_params(
        params: Any,
    ) -> list[Any] | tuple[Any, ...] | dict[str, Any] | None | AgentError:
        if params is None:
            return None
        if isinstance(params, list):
            return params
        if isinstance(params, dict):
            return params
        return build_error(
            TOOL_ARGUMENT_ERROR,
            "SQL query tool params must be an array, an object, or omitted.",
        )

    @staticmethod
    def _normalize_database(value: Any) -> str | None | AgentError:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized

    @staticmethod
    def _normalize_max_rows(value: Any) -> int | AgentError:
        try:
            max_rows = int(value)
        except (TypeError, ValueError):
            return build_error(TOOL_ARGUMENT_ERROR, "SQL query tool max_rows must be an integer.")
        if max_rows < 1 or max_rows > 1000:
            return build_error(TOOL_ARGUMENT_ERROR, "SQL query tool max_rows must be between 1 and 1000.")
        return max_rows

    @staticmethod
    def _error_result(error: AgentError) -> ToolResult:
        return ToolResult(
            output=build_tool_output(success=False, error=error),
            success=False,
            error=error,
        )
