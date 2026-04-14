from __future__ import annotations

from typing import Any

from schemas import AgentError, ToolResult, build_error
from storage import VectorStorage
from tools.tools import BaseTool, build_tool_output


def build_vector_schema_tool_name(backend_name: str) -> str:
    return f"inspect_{backend_name}_schema"


def build_vector_schema_tool_description(backend_name: str) -> str:
    return (
        f"Inspect the authorized collections exposed by the `{backend_name}` vector backend. "
        "Use this before vector search when you are unsure which collection to search."
    )


class VectorSchemaTool(BaseTool):
    parameters = {
        "type": "object",
        "properties": {
            "collection": {
                "type": "string",
                "description": (
                    "Optional collection name to inspect. "
                    "When omitted, the tool returns the available collections."
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
        storage: VectorStorage,
        backend_name: str,
    ) -> None:
        self.name = name
        self.description = description
        self._storage = storage
        self._backend_name = backend_name

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        collection = self._normalize_optional_string(arguments.get("collection"))
        if isinstance(collection, AgentError):
            return self._error_result(collection)

        try:
            result = self._storage.inspect_schema(collection=collection)
        except AgentError as exc:
            return self._error_result(exc)
        except Exception as exc:
            error = build_error("VECTOR_SCHEMA_TOOL_ERROR", f"Vector schema tool failed unexpectedly: {exc}")
            return self._error_result(error)

        return ToolResult(
            output=build_tool_output(success=True, data=result),
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
