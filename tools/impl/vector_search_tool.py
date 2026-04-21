from __future__ import annotations

from typing import Any

from schemas import AgentError, TOOL_ARGUMENT_ERROR, ToolResult, VECTOR_SEARCH_TOOL_ERROR, build_error
from infra.db import VectorSearchRequest, VectorStorage
from tools.tools import BaseTool, build_tool_output


def build_vector_search_tool_name(backend_name: str) -> str:
    return f"search_{backend_name}_vectors"


def build_vector_search_tool_description(backend_name: str) -> str:
    return (
        f"Run semantic vector retrieval against the `{backend_name}` backend. "
        "Use this when the answer depends on concept similarity, fuzzy wording, or paraphrased knowledge."
    )


class VectorSearchTool(BaseTool):
    parameters = {
        "type": "object",
        "properties": {
            "collection": {
                "type": "string",
                "description": (
                    "Collection name to search. "
                    "Optional when only one collection is available; "
                    "use inspect schema first if unsure which collection to use."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Natural language query text for semantic similarity search. "
                    "The query is embedded and matched against stored vectors by concept similarity."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of closest matches to return. Defaults to 3, must be between 1 and 20.",
                "default": 3,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
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
        collection = self._normalize_collection(arguments.get("collection"))
        if isinstance(collection, AgentError):
            return self._error_result(collection)
        query = str(arguments.get("query", "")).strip()
        if not query:
            error = build_error(TOOL_ARGUMENT_ERROR, "Vector search tool requires a non-empty query.")
            return self._error_result(error)

        top_k = self._normalize_top_k(arguments.get("top_k", 3))
        if isinstance(top_k, AgentError):
            return self._error_result(top_k)

        try:
            matches = self._storage.search(
                VectorSearchRequest(
                    collection=collection,
                    query=query,
                    top_k=top_k,
                )
            )
        except AgentError as exc:
            return self._error_result(exc)
        except Exception as exc:
            error = build_error(VECTOR_SEARCH_TOOL_ERROR, f"Vector search tool failed unexpectedly: {exc}")
            return self._error_result(error)

        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "backend": self._backend_name,
                    "collection": collection,
                    "query": query,
                    "top_k": top_k,
                    "match_count": len(matches),
                    "matches": matches,
                },
            ),
            success=True,
        )

    @staticmethod
    def _normalize_top_k(value: object) -> int | AgentError:
        try:
            top_k = int(value)
        except (TypeError, ValueError):
            return build_error(TOOL_ARGUMENT_ERROR, "Vector search tool top_k must be an integer.")
        if top_k < 1 or top_k > 20:
            return build_error(TOOL_ARGUMENT_ERROR, "Vector search tool top_k must be between 1 and 20.")
        return top_k

    @staticmethod
    def _normalize_collection(value: object) -> str | None | AgentError:
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
