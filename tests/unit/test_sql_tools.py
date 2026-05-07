from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from schemas.errors import (
    SQL_QUERY_TOOL_ERROR,
    SQL_SCHEMA_TOOL_ERROR,
    TOOL_ARGUMENT_ERROR,
)
from schemas import PipelineError, SQLQueryRequest
from infra.db.storage import RelationalStorage
from tools.impl.sql_query_tool import (
    SQLQueryTool,
    build_sql_query_tool_description,
    build_sql_query_tool_name,
)
from tools.impl.sql_schema_tool import (
    SQLSchemaTool,
    build_sql_schema_tool_description,
    build_sql_schema_tool_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(query_return=None, schema_return=None, query_side_effect=None, schema_side_effect=None):
    storage = MagicMock(spec=RelationalStorage)
    storage.backend_name = "sqlite"
    if query_side_effect:
        storage.query.side_effect = query_side_effect
    else:
        storage.query.return_value = query_return or []
    if schema_side_effect:
        storage.inspect_schema.side_effect = schema_side_effect
    else:
        storage.inspect_schema.return_value = schema_return or {"tables": []}
    return storage


def _make_query_tool(storage=None, backend="sqlite"):
    s = storage or _make_storage()
    return SQLQueryTool(
        name=build_sql_query_tool_name(backend),
        description=build_sql_query_tool_description(backend),
        storage=s,
        backend_name=backend,
    )


def _make_schema_tool(storage=None, backend="sqlite"):
    s = storage or _make_storage()
    return SQLSchemaTool(
        name=build_sql_schema_tool_name(backend),
        description=build_sql_schema_tool_description(backend),
        storage=s,
        backend_name=backend,
    )


# ===========================================================================
# SQLQueryTool
# ===========================================================================

class TestSQLQueryTool:

    def test_successful_query(self):
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        tool = _make_query_tool(_make_storage(query_return=rows))
        result = tool.run({"statement": "SELECT * FROM users"})
        assert result.success
        data = json.loads(result.output)["data"]
        assert data["row_count"] == 2
        assert data["columns"] == ["id", "name"]
        assert data["rows"] == rows

    def test_empty_result_set(self):
        tool = _make_query_tool(_make_storage(query_return=[]))
        result = tool.run({"statement": "SELECT * FROM empty"})
        assert result.success
        data = json.loads(result.output)["data"]
        assert data["row_count"] == 0
        assert data["columns"] == []

    def test_statement_passed_to_storage(self):
        storage = _make_storage(query_return=[])
        tool = _make_query_tool(storage)
        tool.run({"statement": "SELECT 1"})
        call_args = storage.query.call_args[0][0]
        assert call_args.statement == "SELECT 1"

    def test_database_passed_to_storage(self):
        storage = _make_storage(query_return=[])
        tool = _make_query_tool(storage)
        tool.run({"statement": "SELECT 1", "database": "mydb"})
        call_args = storage.query.call_args[0][0]
        assert call_args.database == "mydb"

    def test_params_list_passed(self):
        storage = _make_storage(query_return=[])
        tool = _make_query_tool(storage)
        tool.run({"statement": "SELECT * FROM t WHERE id=?", "params": [42]})
        call_args = storage.query.call_args[0][0]
        assert call_args.params == [42]

    def test_params_dict_passed(self):
        storage = _make_storage(query_return=[])
        tool = _make_query_tool(storage)
        tool.run({"statement": "SELECT * FROM t WHERE id=:id", "params": {"id": 1}})
        call_args = storage.query.call_args[0][0]
        assert call_args.params == {"id": 1}

    def test_max_rows_passed(self):
        storage = _make_storage(query_return=[])
        tool = _make_query_tool(storage)
        tool.run({"statement": "SELECT 1", "max_rows": 10})
        call_args = storage.query.call_args[0][0]
        assert call_args.max_rows == 10

    def test_empty_statement_fails(self):
        tool = _make_query_tool()
        result = tool.run({"statement": ""})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_missing_statement_fails(self):
        tool = _make_query_tool()
        result = tool.run({})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_invalid_max_rows_fails(self):
        tool = _make_query_tool()
        result = tool.run({"statement": "SELECT 1", "max_rows": 0})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_max_rows_too_large_fails(self):
        tool = _make_query_tool()
        result = tool.run({"statement": "SELECT 1", "max_rows": 9999})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_invalid_params_type_fails(self):
        tool = _make_query_tool()
        result = tool.run({"statement": "SELECT 1", "params": "bad"})
        assert not result.success
        assert result.error.code == TOOL_ARGUMENT_ERROR

    def test_storage_agent_error_propagated(self):
        err = PipelineError(code=SQL_QUERY_TOOL_ERROR, message="db error")
        tool = _make_query_tool(_make_storage(query_side_effect=err))
        result = tool.run({"statement": "SELECT 1"})
        assert not result.success
        assert result.error.code == SQL_QUERY_TOOL_ERROR

    def test_storage_unexpected_exception(self):
        tool = _make_query_tool(_make_storage(query_side_effect=RuntimeError("boom")))
        result = tool.run({"statement": "SELECT 1"})
        assert not result.success
        assert result.error.code == SQL_QUERY_TOOL_ERROR

    def test_output_includes_backend_name(self):
        tool = _make_query_tool(_make_storage(query_return=[]), backend="mysql")
        result = tool.run({"statement": "SELECT 1"})
        data = json.loads(result.output)["data"]
        assert data["backend"] == "mysql"

    def test_tool_name_sqlite(self):
        tool = _make_query_tool(backend="sqlite")
        assert tool.name == "query_sqlite_data"

    def test_tool_name_mysql(self):
        tool = _make_query_tool(backend="mysql")
        assert tool.name == "query_mysql_data"

    def test_tool_schema(self):
        tool = _make_query_tool()
        schema = tool.schema()
        assert "statement" in schema["parameters"]["properties"]


# ===========================================================================
# SQLSchemaTool
# ===========================================================================

class TestSQLSchemaTool:

    def test_successful_schema_inspection(self):
        schema_data = {"tables": ["users", "orders"]}
        tool = _make_schema_tool(_make_storage(schema_return=schema_data))
        result = tool.run({})
        assert result.success
        data = json.loads(result.output)["data"]
        assert data == schema_data

    def test_database_passed_to_storage(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({"database": "mydb"})
        storage.inspect_schema.assert_called_once_with(database="mydb", table=None)

    def test_table_passed_to_storage(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({"table": "users"})
        storage.inspect_schema.assert_called_once_with(database=None, table="users")

    def test_empty_database_treated_as_none(self):
        storage = _make_storage(schema_return={})
        tool = _make_schema_tool(storage)
        tool.run({"database": "  "})
        storage.inspect_schema.assert_called_once_with(database=None, table=None)

    def test_storage_agent_error_propagated(self):
        err = PipelineError(code=SQL_SCHEMA_TOOL_ERROR, message="schema error")
        tool = _make_schema_tool(_make_storage(schema_side_effect=err))
        result = tool.run({})
        assert not result.success
        assert result.error.code == SQL_SCHEMA_TOOL_ERROR

    def test_storage_unexpected_exception(self):
        tool = _make_schema_tool(_make_storage(schema_side_effect=RuntimeError("boom")))
        result = tool.run({})
        assert not result.success
        assert result.error.code == SQL_SCHEMA_TOOL_ERROR

    def test_tool_name_sqlite(self):
        tool = _make_schema_tool(backend="sqlite")
        assert tool.name == "inspect_sqlite_schema"

    def test_tool_name_mysql(self):
        tool = _make_schema_tool(backend="mysql")
        assert tool.name == "inspect_mysql_schema"

    def test_tool_schema(self):
        tool = _make_schema_tool()
        schema = tool.schema()
        assert "database" in schema["parameters"]["properties"]


# ===========================================================================
# Name/description builders
# ===========================================================================

def test_query_tool_name_builder():
    assert build_sql_query_tool_name("sqlite") == "query_sqlite_data"
    assert build_sql_query_tool_name("mysql") == "query_mysql_data"


def test_schema_tool_name_builder():
    assert build_sql_schema_tool_name("sqlite") == "inspect_sqlite_schema"
    assert build_sql_schema_tool_name("mysql") == "inspect_mysql_schema"


def test_query_tool_description_contains_backend():
    desc = build_sql_query_tool_description("sqlite")
    assert "sqlite" in desc.lower() or "SQLite" in desc


def test_schema_tool_description_contains_backend():
    desc = build_sql_schema_tool_description("sqlite")
    assert "sqlite" in desc.lower() or "SQLite" in desc
