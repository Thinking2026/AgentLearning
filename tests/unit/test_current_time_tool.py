from __future__ import annotations

import json
from datetime import datetime

import pytest

from tools.impl.current_time_tool import CurrentTimeTool


@pytest.fixture
def tool():
    return CurrentTimeTool()


def test_returns_success(tool):
    result = tool.run({})
    assert result.success


def test_output_has_current_time_field(tool):
    result = tool.run({})
    data = json.loads(result.output)["data"]
    assert "current_time" in data


def test_current_time_is_string(tool):
    result = tool.run({})
    data = json.loads(result.output)["data"]
    assert isinstance(data["current_time"], str)


def test_current_time_is_parseable_iso(tool):
    result = tool.run({})
    data = json.loads(result.output)["data"]
    ts = data["current_time"]
    # Should be parseable as an ISO-format datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed.year >= 2024


def test_ignores_extra_arguments(tool):
    result = tool.run({"unexpected_key": "value"})
    assert result.success


def test_tool_name(tool):
    assert tool.name == "current_time"


def test_tool_schema(tool):
    schema = tool.schema()
    assert schema["name"] == "current_time"
    assert schema["parameters"]["properties"] == {}
