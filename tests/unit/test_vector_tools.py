from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from schemas.errors import (
    TOOL_ARGUMENT_ERROR,
    VECTOR_SCHEMA_TOOL_ERROR,
    VECTOR_SEARCH_TOOL_ERROR,
)
from schemas import AgentError, VectorSearchRequest
from infra.db.storage import VectorStorage
from tools.impl.vector_search_tool import (
    VectorSearchTool,
    build_vector_search_tool_description,
    build_vector_search_tool_name,
)
from tools.impl.vector_schema_tool import (
    VectorSchemaTool,
    build_vector_schema_tool_description,
    build_vector_schema_tool_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(search_return=None, schema_return=None, search_side_effect=None, schema_side_effect=None):
    storage = MagicMock(spec=VectorStorage)
    storage.backend_name = "chromadb"
    if search_side_effect:
        storage.search.side_effect = search_side_effect
    else:
        storage.search.return_value = search_return or []
    if schema_side_effect:
        storage.inspect_schema.side_effect = schema_side_effect
    else:
        storage.inspect_schema.return_value = schema_return or {"collections": []}
    return storage


def _make_search_tool(storage=None, backend="chromadb"):
    s = storage or _make_storage()
    return VectorSearchTool(
        name=build_vector_search_tool_name(backend),
        description=build_vector_search_tool_description(backend),
        storage=s,
        backend_name=backend,
    )


def _make_schema_tool(storage=None, backend="chromadb"):
    s = storage or _make_storage()
    return VectorSchemaTool(
        name=build_vector_schema_tool_name(backend),
        description=build_vector_schema_tool_description(backend),
        storage=s,
        backend_name=backend,
    )


# ===========================================================================
# VectorSearchTool
# ===========================================================================

class TestVectorSearchTool:

    def test_successful_search(self):
        matches = [{"id": "1", "text": "hello", "score": 0.9}]
        tool = _make_search_tool(_make_storage(search_return=matches))
        result = tool.run({"query": "hello world"})
        assert result.success
        data = json.loads(result.output)["data"]
        assert data["match_count"] == 1
        assert data["matches"] == matches

    def test_query_passed_to_storage(self):
        storage = _make_storage()
        tool = _make_search_tool(storage)
        tool.run({"query": "test query"})
        call_args = storage.search.call_args[0][0]
        assert call_args.query == "test query"

    def test_collection_passed_to_storage(self):
        storage = _make_storage()
        tool = _make_search_tool(storage)
        tool.run({"query": "test", "collection": "my_col"})
        call_args = storage.search.call_args[0][0]
        assert call_args.collection == "my_col"

    def test_top_k_passed_to_storage(self):
        storage = _make_storage()
        tool = _make_search_tool(storage)
        tool.run({"query": "test", "top_k": 5})
        call_args = storage.search.call_args[0][0]
        assert call_args.top_k == 5

    def test_empty_collection_treated_as_none(self):
        storage = _make_storage()
        tool = _make_search_tool(storage)
        tool.run({"query": "test", "collection": "  "})
        call_args = storage.search.call_args[0][0]
        assert call_args.collection is None

    def test_output_includes_backend_name(self):
        tool = _make_search_tool(backend="chromadb")
        result = tool.run({"query": "test"})
        data = json.loads(result.output)["data"]
        assert data["backend"] == "chromadb"

    def test_empty_query_fails(self):
        tool = _make_search_tool()
        result = tool.run({"query": ""})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_missing_query_fails(self):
        tool = _make_search_tool()
        result = tool.run({})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_top_k_too_small_fails(self):
        tool = _make_search_tool()
        result = tool.run({"query": "test", "top_k": 0})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_top_k_too_large_fails(self):
        tool = _make_search_tool()
        result = tool.run({"query": "test", "top_k": 21})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_invalid_top_k_type_fails(self):
        tool = _make_search_tool()
        result = tool.run({"query": "test", "top_k": "bad"})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_storage_agent_error_propagated(self):
        err = AgentError(code=VECTOR_SEARCH_TOOL_ERROR, message="search error")
        tool = _make_search_tool(_make_storage(search_side_effect=err))
        result = tool.run({"query": "test"})
        assert not result.success
        assert result.error.code == VECTOR_SEARCH_TOOL_ERROR

    def test_storage_unexpected_exception(self):
        tool = _make_search_tool(_make_storage(search_side_effect=RuntimeError("boom")))
        result = tool.run({"query": "test"})
        assert not result.success
        assert result.error.code == VECTOR_SEARCH_TOOL_ERROR

    def test_tool_name(self):
        tool = _make_search_tool(backend="chromadb")
        assert tool.name == "search_chromadb_vectors"

    def test_tool_schema(self):
        tool = _make_search_tool()
        schema = tool.schema()
        assert "query" in schema["parameters"]["properties"]


# ===========================================================================
# VectorSchemaTool
# ===========================================================================

class TestVectorSchemaTool:

    def test_successful_schema_inspection(self):
        schema_data = {"collections": ["docs", "faq"]}
        tool = _make_schema_tool(_make_storage(schema_return=schema_data))
        result = tool.run({})
        assert result.success
        data = json.loads(result.output)["data"]
        assert data == schema_data

    def test_collection_passed_to_storage(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({"collection": "docs"})
        storage.inspect_schema.assert_called_once_with(collection="docs")

    def test_empty_collection_treated_as_none(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({"collection": "  "})
        storage.inspect_schema.assert_called_once_with(collection=None)

    def test_no_collection_passes_none(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({})
        storage.inspect_schema.assert_called_once_with(collection=None)

    def test_storage_agent_error_propagated(self):
        err = AgentError(code=VECTOR_SCHEMA_TOOL_ERROR, message="schema error")
        tool = _make_schema_tool(_make_storage(schema_side_effect=err))
        result = tool.run({})
        assert not result.success
        assert result.error.code == VECTOR_SCHEMA_TOOL_ERROR

    def test_storage_unexpected_exception(self):
        tool = _make_schema_tool(_make_storage(schema_side_effect=RuntimeError("boom")))
        result = tool.run({})
        assert not result.success
        assert result.error.code == VECTOR_SCHEMA_TOOL_ERROR

    def test_tool_name(self):
        tool = _make_schema_tool(backend="chromadb")
        assert tool.name == "inspect_chromadb_schema"

    def test_tool_schema(self):
        tool = _make_schema_tool()
        schema = tool.schema()
        assert "collection" in schema["parameters"]["properties"]


# ===========================================================================
# Name/description builders
# ===========================================================================

def test_search_tool_name_builder():
    assert build_vector_search_tool_name("chromadb") == "search_chromadb_vectors"


def test_schema_tool_name_builder():
    assert build_vector_schema_tool_name("chromadb") == "inspect_chromadb_schema"


def test_search_tool_description_contains_backend():
    desc = build_vector_search_tool_description("chromadb")
    assert "chromadb" in desc.lower()


def test_schema_tool_description_contains_backend():
    desc = build_vector_schema_tool_description("chromadb")
    assert "chromadb" in desc.lower()
