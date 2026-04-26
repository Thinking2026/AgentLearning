from __future__ import annotations

import json
import sys

import pytest

from schemas.errors import SHELL_COMMAND_FAILED, SHELL_TIMEOUT, TOOL_ARGUMENT_ERROR
from tools.impl.shell_tool import ShellTool
from utils.env_util.runtime_env import TASK_RUNTIME_DIR_ENV


@pytest.fixture
def tool():
    return ShellTool()


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(TASK_RUNTIME_DIR_ENV, str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Basic success cases
# ---------------------------------------------------------------------------

def test_echo_command(tool, tmp_workspace):
    result = tool.run({"command": "echo hello"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "hello" in data["stdout"]
    assert data["exit_code"] == 0


def test_returns_cwd(tool, tmp_workspace):
    result = tool.run({"command": "echo hello"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "cwd" in data


def test_multiline_output(tool, tmp_workspace):
    result = tool.run({"command": "printf 'line1\\nline2\\nline3'"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "line1" in data["stdout"]
    assert "line2" in data["stdout"]


def test_command_with_pipe(tool, tmp_workspace):
    result = tool.run({"command": "echo hello | tr a-z A-Z"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "HELLO" in data["stdout"]


def test_command_creates_file(tool, tmp_workspace):
    result = tool.run({"command": f"touch {tmp_workspace}/created.txt"})
    assert result.success
    assert (tmp_workspace / "created.txt").exists()


def test_command_reads_file(tool, tmp_workspace):
    (tmp_workspace / "data.txt").write_text("file content")
    result = tool.run({"command": "cat data.txt"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "file content" in data["stdout"]


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------

def test_nonzero_exit_code_fails(tool, tmp_workspace):
    result = tool.run({"command": "exit 1"})
    assert not result.success
    assert result.error.code == SHELL_COMMAND_FAILED


def test_command_not_found_fails(tool, tmp_workspace):
    result = tool.run({"command": "this_command_does_not_exist_xyz"})
    assert not result.success
    assert result.error.code == SHELL_COMMAND_FAILED


def test_empty_command_fails(tool, tmp_workspace):
    result = tool.run({"command": ""})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_whitespace_only_command_fails(tool, tmp_workspace):
    result = tool.run({"command": "   "})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_missing_command_key_fails(tool, tmp_workspace):
    result = tool.run({})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_kills_command(tool, tmp_workspace):
    result = tool.run({"command": "sleep 60", "timeout": 1})
    assert not result.success
    assert result.error.code == SHELL_TIMEOUT


# ---------------------------------------------------------------------------
# Stderr captured
# ---------------------------------------------------------------------------

def test_stderr_captured_on_success(tool, tmp_workspace):
    result = tool.run({"command": "echo warn >&2 && echo out"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "warn" in data["stderr"]
    assert "out" in data["stdout"]


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name(tool):
    assert tool.name == "shell"


def test_tool_schema(tool):
    schema = tool.schema()
    assert schema["name"] == "shell"
    assert "command" in schema["parameters"]["properties"]
