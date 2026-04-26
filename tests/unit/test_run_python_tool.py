from __future__ import annotations

import json

import pytest

from schemas.errors import (
    PYTHON_TOOL_ERROR,
    PYTHON_TOOL_FORBIDDEN_IMPORT,
    PYTHON_TOOL_TIMEOUT,
    TOOL_ARGUMENT_ERROR,
)
from tools.impl.run_python_tool import RunPythonTool, _check_imports


@pytest.fixture
def tool():
    return RunPythonTool()


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------

def test_print_output(tool):
    result = tool.run({"code": "print('hello world')"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "hello world" in data["output"]


def test_arithmetic_output(tool):
    result = tool.run({"code": "print(2 + 2)"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "4" in data["output"]


def test_multiline_code(tool):
    code = "x = 10\ny = 20\nprint(x + y)"
    result = tool.run({"code": code})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "30" in data["output"]


def test_no_print_produces_empty_output(tool):
    result = tool.run({"code": "x = 42"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert data["output"] == ""


# ---------------------------------------------------------------------------
# Allowed imports
# ---------------------------------------------------------------------------

def test_import_math(tool):
    result = tool.run({"code": "import math\nprint(math.pi)"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "3.14" in data["output"]


def test_import_json(tool):
    result = tool.run({"code": "import json\nprint(json.dumps({'a': 1}))"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert '"a"' in data["output"]


def test_import_datetime(tool):
    result = tool.run({"code": "import datetime\nprint(type(datetime.datetime.now()))"})
    assert result.success


# ---------------------------------------------------------------------------
# Forbidden imports
# ---------------------------------------------------------------------------

def test_import_os_blocked(tool):
    result = tool.run({"code": "import os"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_FORBIDDEN_IMPORT


def test_import_subprocess_blocked(tool):
    result = tool.run({"code": "import subprocess"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_FORBIDDEN_IMPORT


def test_import_sys_blocked(tool):
    result = tool.run({"code": "import sys"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_FORBIDDEN_IMPORT


def test_import_unknown_module_blocked(tool):
    result = tool.run({"code": "import totally_unknown_module_xyz"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_FORBIDDEN_IMPORT


# ---------------------------------------------------------------------------
# Context / session variables
# ---------------------------------------------------------------------------

def test_context_vars_persisted(tool):
    tool.run({"code": "x = 42", "context_vars": ["x"]})
    result = tool.run({"code": "print(x)"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "42" in data["output"]


def test_context_injection_for_single_call(tool):
    result = tool.run({"code": "print(y)", "context": {"y": 99}})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "99" in data["output"]


def test_context_injection_not_persisted_without_context_vars(tool):
    tool.run({"code": "print(z)", "context": {"z": 7}})
    result = tool.run({"code": "print(z)"})
    assert not result.success  # z not in session


def test_reset_context_clears_session(tool):
    tool.run({"code": "a = 1", "context_vars": ["a"]})
    tool.run({"action": "reset_context"})
    result = tool.run({"code": "print(a)"})
    assert not result.success


def test_reset_context_action(tool):
    result = tool.run({"action": "reset_context"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert data["action"] == "reset_context"


def test_callable_not_saved_to_session(tool):
    result = tool.run({"code": "def f(): pass", "context_vars": ["f"]})
    assert result.success
    data = json.loads(result.output)["data"]
    assert "f" not in data["context"]


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_kills_execution(tool):
    result = tool.run({"code": "while True: pass", "timeout": 1})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_TIMEOUT


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def test_empty_code_fails(tool):
    result = tool.run({"code": ""})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_missing_code_fails(tool):
    result = tool.run({})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


# ---------------------------------------------------------------------------
# Runtime errors
# ---------------------------------------------------------------------------

def test_runtime_error_returns_failure(tool):
    result = tool.run({"code": "1 / 0"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_ERROR


def test_name_error_returns_failure(tool):
    result = tool.run({"code": "print(undefined_var)"})
    assert not result.success
    assert result.error.code == PYTHON_TOOL_ERROR


# ---------------------------------------------------------------------------
# _check_imports helper
# ---------------------------------------------------------------------------

def test_check_imports_allowed():
    assert _check_imports("import math") == []


def test_check_imports_blocked():
    forbidden = _check_imports("import os")
    assert "os" in forbidden


def test_check_imports_from_blocked():
    forbidden = _check_imports("from subprocess import run")
    assert "subprocess" in forbidden


def test_check_imports_unknown():
    forbidden = _check_imports("import some_unknown_lib")
    assert "some_unknown_lib" in forbidden


def test_check_imports_syntax_error_returns_empty():
    assert _check_imports("def (") == []


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name(tool):
    assert tool.name == "run_python"


def test_tool_schema(tool):
    schema = tool.schema()
    assert schema["name"] == "run_python"
    assert "code" in schema["parameters"]["properties"]


def test_reset_clears_session(tool):
    tool.run({"code": "v = 5", "context_vars": ["v"]})
    tool.reset()
    result = tool.run({"code": "print(v)"})
    assert not result.success
