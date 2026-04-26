from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from schemas.errors import FILE_TOOL_ERROR, TOOL_ARGUMENT_ERROR
from tools.impl.file_tool import FileTool
from utils.env_util.runtime_env import TASK_RUNTIME_DIR_ENV


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(TASK_RUNTIME_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def tool():
    return FileTool()


# ---------------------------------------------------------------------------
# write + read
# ---------------------------------------------------------------------------

def test_write_and_read(tool, tmp_workspace):
    write_result = tool.run({"action": "write", "path": "test.txt", "content": "hello world"})
    assert write_result.success

    read_result = tool.run({"action": "read", "path": "test.txt"})
    assert read_result.success
    data = json.loads(read_result.output)["data"]
    assert data["content"] == "hello world"


def test_write_creates_parent_dirs(tool, tmp_workspace):
    result = tool.run({"action": "write", "path": "subdir/nested/file.txt", "content": "data"})
    assert result.success
    assert (tmp_workspace / "subdir" / "nested" / "file.txt").exists()


def test_write_overwrites_existing(tool, tmp_workspace):
    tool.run({"action": "write", "path": "f.txt", "content": "original"})
    tool.run({"action": "write", "path": "f.txt", "content": "updated"})
    read_result = tool.run({"action": "read", "path": "f.txt"})
    assert json.loads(read_result.output)["data"]["content"] == "updated"


def test_write_returns_bytes_written(tool, tmp_workspace):
    content = "hello"
    result = tool.run({"action": "write", "path": "f.txt", "content": content})
    data = json.loads(result.output)["data"]
    assert data["bytes_written"] == len(content.encode("utf-8"))


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------

def test_append_to_existing(tool, tmp_workspace):
    tool.run({"action": "write", "path": "a.txt", "content": "line1\n"})
    tool.run({"action": "append", "path": "a.txt", "content": "line2\n"})
    read_result = tool.run({"action": "read", "path": "a.txt"})
    content = json.loads(read_result.output)["data"]["content"]
    assert content == "line1\nline2\n"


def test_append_creates_file(tool, tmp_workspace):
    result = tool.run({"action": "append", "path": "new.txt", "content": "data"})
    assert result.success
    assert (tmp_workspace / "new.txt").exists()


# ---------------------------------------------------------------------------
# read errors
# ---------------------------------------------------------------------------

def test_read_nonexistent_file(tool, tmp_workspace):
    result = tool.run({"action": "read", "path": "nonexistent.txt"})
    assert not result.success
    assert result.error.code == FILE_TOOL_ERROR


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def test_list_dir(tool, tmp_workspace):
    (tmp_workspace / "file1.txt").write_text("a")
    (tmp_workspace / "file2.txt").write_text("b")
    (tmp_workspace / "subdir").mkdir()

    result = tool.run({"action": "list_dir", "path": str(tmp_workspace)})
    assert result.success
    data = json.loads(result.output)["data"]
    names = [e["name"] for e in data["entries"]]
    assert "file1.txt" in names
    assert "file2.txt" in names
    assert "subdir" in names


def test_list_dir_shows_types(tool, tmp_workspace):
    (tmp_workspace / "f.txt").write_text("x")
    (tmp_workspace / "d").mkdir()

    result = tool.run({"action": "list_dir", "path": str(tmp_workspace)})
    data = json.loads(result.output)["data"]
    types = {e["name"]: e["type"] for e in data["entries"]}
    assert types["f.txt"] == "file"
    assert types["d"] == "directory"


def test_list_dir_nonexistent(tool, tmp_workspace):
    result = tool.run({"action": "list_dir", "path": str(tmp_workspace / "missing")})
    assert not result.success
    assert result.error.code == FILE_TOOL_ERROR


def test_list_dir_on_file_fails(tool, tmp_workspace):
    (tmp_workspace / "f.txt").write_text("x")
    result = tool.run({"action": "list_dir", "path": str(tmp_workspace / "f.txt")})
    assert not result.success
    assert result.error.code == FILE_TOOL_ERROR


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def test_invalid_action(tool, tmp_workspace):
    result = tool.run({"action": "delete", "path": "f.txt"})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_empty_path(tool, tmp_workspace):
    result = tool.run({"action": "read", "path": ""})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_missing_action(tool, tmp_workspace):
    result = tool.run({"path": "f.txt"})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


# ---------------------------------------------------------------------------
# Absolute path
# ---------------------------------------------------------------------------

def test_absolute_path_write_read(tool, tmp_path):
    abs_path = str(tmp_path / "abs_test.txt")
    write_result = tool.run({"action": "write", "path": abs_path, "content": "absolute"})
    assert write_result.success
    read_result = tool.run({"action": "read", "path": abs_path})
    assert json.loads(read_result.output)["data"]["content"] == "absolute"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name(tool):
    assert tool.name == "file"


def test_tool_schema(tool):
    schema = tool.schema()
    assert schema["name"] == "file"
    assert "action" in schema["parameters"]["properties"]
    assert "path" in schema["parameters"]["properties"]
