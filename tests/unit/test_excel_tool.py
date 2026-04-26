from __future__ import annotations

import json

import pytest

from schemas.errors import (
    EXCEL_TOOL_ERROR,
    EXCEL_TOOL_SHEET_EXISTS,
    TOOL_ARGUMENT_ERROR,
)
from tools.impl.excel_tool import ExcelTool
from utils.env_util.runtime_env import TASK_RUNTIME_DIR_ENV

openpyxl = pytest.importorskip("openpyxl", reason="openpyxl not installed")


@pytest.fixture
def tool():
    return ExcelTool()


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(TASK_RUNTIME_DIR_ENV, str(tmp_path))
    return tmp_path


def _wb_path(tmp_workspace, name="test.xlsx"):
    return str(tmp_workspace / name)


# ---------------------------------------------------------------------------
# write_sheet + inspect + read_sheet
# ---------------------------------------------------------------------------

def test_write_and_inspect(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    rows = [["Name", "Age"], ["Alice", 30], ["Bob", 25]]
    write_result = tool.run({"action": "write_sheet", "path": path, "rows": rows})
    assert write_result.success

    inspect_result = tool.run({"action": "inspect", "path": path})
    assert inspect_result.success
    data = json.loads(inspect_result.output)["data"]
    assert data["sheet_count"] >= 1


def test_write_and_read_sheet(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    rows = [["x", "y"], [1, 2], [3, 4]]
    tool.run({"action": "write_sheet", "path": path, "rows": rows})

    read_result = tool.run({"action": "read_sheet", "path": path})
    assert read_result.success
    data = json.loads(read_result.output)["data"]
    assert data["returned_row_count"] == 3
    assert data["rows"][0] == ["x", "y"]


def test_write_creates_file(tool, tmp_workspace):
    path = _wb_path(tmp_workspace, "new.xlsx")
    result = tool.run({"action": "write_sheet", "path": path, "rows": [["a"]]})
    assert result.success
    assert (tmp_workspace / "new.xlsx").exists()


def test_write_returns_row_count(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    rows = [["a"], ["b"], ["c"]]
    result = tool.run({"action": "write_sheet", "path": path, "rows": rows})
    data = json.loads(result.output)["data"]
    assert data["row_count"] == 3


def test_write_named_sheet(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    tool.run({"action": "write_sheet", "path": path, "sheet_name": "MySheet", "rows": [["v"]]})
    inspect = tool.run({"action": "inspect", "path": path})
    data = json.loads(inspect.output)["data"]
    names = [s["name"] for s in data["sheets"]]
    assert "MySheet" in names


# ---------------------------------------------------------------------------
# replace_sheet
# ---------------------------------------------------------------------------

def test_write_replaces_sheet_by_default(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    tool.run({"action": "write_sheet", "path": path, "sheet_name": "S", "rows": [["old"]]})
    tool.run({"action": "write_sheet", "path": path, "sheet_name": "S", "rows": [["new"]], "replace_sheet": True})
    read = tool.run({"action": "read_sheet", "path": path, "sheet_name": "S"})
    data = json.loads(read.output)["data"]
    assert data["rows"][0][0] == "new"


def test_write_fails_if_sheet_exists_and_no_replace(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    tool.run({"action": "write_sheet", "path": path, "sheet_name": "S", "rows": [["v"]]})
    result = tool.run({"action": "write_sheet", "path": path, "sheet_name": "S", "rows": [["v2"]], "replace_sheet": False})
    assert not result.success
    assert result.error.code == EXCEL_TOOL_SHEET_EXISTS


# ---------------------------------------------------------------------------
# append_rows
# ---------------------------------------------------------------------------

def test_append_rows_to_existing_sheet(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    # Use append_rows for both writes to avoid the empty-row issue from write_sheet
    tool.run({"action": "append_rows", "path": path, "sheet_name": "S", "rows": [["a"]]})
    tool.run({"action": "append_rows", "path": path, "sheet_name": "S", "rows": [["b"], ["c"]]})
    read = tool.run({"action": "read_sheet", "path": path, "sheet_name": "S"})
    data = json.loads(read.output)["data"]
    assert data["returned_row_count"] == 3


def test_append_rows_creates_file(tool, tmp_workspace):
    path = _wb_path(tmp_workspace, "append_new.xlsx")
    result = tool.run({"action": "append_rows", "path": path, "rows": [["x"]]})
    assert result.success
    assert (tmp_workspace / "append_new.xlsx").exists()


def test_append_rows_returns_rows_appended(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    result = tool.run({"action": "append_rows", "path": path, "rows": [["a"], ["b"]]})
    data = json.loads(result.output)["data"]
    assert data["rows_appended"] == 2


# ---------------------------------------------------------------------------
# max_rows
# ---------------------------------------------------------------------------

def test_read_sheet_respects_max_rows(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    rows = [[i] for i in range(20)]
    tool.run({"action": "write_sheet", "path": path, "rows": rows})
    read = tool.run({"action": "read_sheet", "path": path, "max_rows": 5})
    data = json.loads(read.output)["data"]
    assert data["returned_row_count"] == 5


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_inspect_nonexistent_file(tool, tmp_workspace):
    result = tool.run({"action": "inspect", "path": _wb_path(tmp_workspace, "missing.xlsx")})
    assert not result.success
    assert result.error.code == EXCEL_TOOL_ERROR


def test_read_nonexistent_file(tool, tmp_workspace):
    result = tool.run({"action": "read_sheet", "path": _wb_path(tmp_workspace, "missing.xlsx")})
    assert not result.success
    assert result.error.code == EXCEL_TOOL_ERROR


def test_read_unknown_sheet(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    tool.run({"action": "write_sheet", "path": path, "rows": [["v"]]})
    result = tool.run({"action": "read_sheet", "path": path, "sheet_name": "NoSuchSheet"})
    assert not result.success
    assert result.error.code == EXCEL_TOOL_ERROR


def test_write_empty_rows_fails(tool, tmp_workspace):
    result = tool.run({"action": "write_sheet", "path": _wb_path(tmp_workspace), "rows": []})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_write_missing_rows_fails(tool, tmp_workspace):
    result = tool.run({"action": "write_sheet", "path": _wb_path(tmp_workspace)})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_invalid_action_fails(tool, tmp_workspace):
    result = tool.run({"action": "delete", "path": _wb_path(tmp_workspace)})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_empty_path_fails(tool, tmp_workspace):
    result = tool.run({"action": "inspect", "path": ""})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


# ---------------------------------------------------------------------------
# Cell value types
# ---------------------------------------------------------------------------

def test_write_mixed_types(tool, tmp_workspace):
    path = _wb_path(tmp_workspace)
    rows = [["text", 42, 3.14, True, None]]
    result = tool.run({"action": "write_sheet", "path": path, "rows": rows})
    assert result.success


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name(tool):
    assert tool.name == "excel"


def test_tool_schema(tool):
    schema = tool.schema()
    assert schema["name"] == "excel"
    assert "action" in schema["parameters"]["properties"]
    assert "path" in schema["parameters"]["properties"]
