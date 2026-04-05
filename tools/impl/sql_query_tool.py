from __future__ import annotations

from typing import Any

from schemas import AgentError, ToolResult, build_error
from storage import BaseStorage
from tools.tools import BaseTool, build_tool_output


def build_sql_query_tool_name(backend_name: str) -> str:
    return f"query_{backend_name}_data"


def build_sql_query_tool_description(backend_name: str) -> str:
    if backend_name == "sqlite":
        return (
            "Run a single read-only SELECT query against the SQLite database. "
            "Use this for relational tables with custom schemas. "
            "Provide SQL plus parameters, and inspect schema first when needed."
        )
    if backend_name == "mysql":
        return (
            "Run a single read-only SELECT query against the MySQL database. "
            "Use this for relational tables with custom schemas. "
            "Provide SQL plus parameters, and inspect schema first when needed."
        )
    return (
        f"Run a single read-only SELECT query against the `{backend_name}` relational backend. "
        "Use this when the answer depends on structured table data."
    )


class SQLQueryTool(BaseTool):
    parameters = {
        "type": "object",
        "properties": {
            "statement": {
                "type": "string",
                "description": (
                    "A single read-only SELECT statement. "
                    "Use placeholders instead of interpolating user values directly."
                ),
            },
            "params": {
                "description": (
                    "Query parameters passed separately from the SQL statement. "
                    "Use an array for positional parameters or an object for named parameters."
                ),
                "oneOf": [
                    {"type": "array", "items": {}},
                    {"type": "object"},
                ],
            },
            "max_rows": {
                "type": "integer",
                "description": "Maximum number of rows to return.",
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
        storage: BaseStorage,
        backend_name: str,
    ) -> None:
        self.name = name
        self.description = description
        self._storage = storage
        self._backend_name = backend_name

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        statement = str(arguments.get("statement", "")).strip()
        if not statement:
            error = build_error("TOOL_ARGUMENT_ERROR", "SQL query tool requires a non-empty statement.")
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
                statement=statement,
                params=normalized_params,
                max_rows=max_rows,
            )
        except AgentError as exc:
            return self._error_result(exc)
        except Exception as exc:
            error = build_error("SQL_QUERY_TOOL_ERROR", f"SQL query tool failed unexpectedly: {exc}")
            return self._error_result(error)

        columns = list(rows[0].keys()) if rows else []
        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "backend": self._backend_name,
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
            "TOOL_ARGUMENT_ERROR",
            "SQL query tool params must be an array, an object, or omitted.",
        )

    @staticmethod
    def _normalize_max_rows(value: Any) -> int | AgentError:
        try:
            max_rows = int(value)
        except (TypeError, ValueError):
            return build_error("TOOL_ARGUMENT_ERROR", "SQL query tool max_rows must be an integer.")
        if max_rows < 1 or max_rows > 1000:
            return build_error("TOOL_ARGUMENT_ERROR", "SQL query tool max_rows must be between 1 and 1000.")
        return max_rows

    @staticmethod
    def _error_result(error: AgentError) -> ToolResult:
        return ToolResult(
            output=build_tool_output(success=False, error=error),
            success=False,
            error=error,
        )
