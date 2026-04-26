from __future__ import annotations

import os
from pathlib import Path

import pytest

from utils.env_util.runtime_env import (
    PROJECT_ROOT_ENV,
    TASK_NAME_ENV,
    TASK_SOURCE_DIR_ENV,
    TASK_RUNTIME_DIR_ENV,
    TASK_PROMPT_FILE_ENV,
    TIMEZONE_ENV,
    set_project_root,
    get_project_root,
    set_task_environment,
    get_task_name,
    get_task_source_dir,
    get_task_runtime_dir,
    get_task_prompt_file,
    set_timezone_name,
    get_timezone_name,
)


# ---------------------------------------------------------------------------
# project root
# ---------------------------------------------------------------------------

def test_set_and_get_project_root(tmp_path, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    result = set_project_root(tmp_path)
    assert result == tmp_path.resolve()
    assert get_project_root() == tmp_path.resolve()


def test_get_project_root_not_set_raises(monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match="not initialized"):
        get_project_root()


def test_set_project_root_expands_user(tmp_path, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    result = set_project_root(str(tmp_path))
    assert result.is_absolute()


# ---------------------------------------------------------------------------
# task environment
# ---------------------------------------------------------------------------

def test_set_and_get_task_environment(tmp_path, monkeypatch):
    monkeypatch.delenv(TASK_NAME_ENV, raising=False)
    monkeypatch.delenv(TASK_SOURCE_DIR_ENV, raising=False)
    monkeypatch.delenv(TASK_RUNTIME_DIR_ENV, raising=False)
    monkeypatch.delenv(TASK_PROMPT_FILE_ENV, raising=False)

    source_dir = tmp_path / "source"
    runtime_dir = tmp_path / "runtime"
    prompt_file = tmp_path / "prompt.txt"

    set_task_environment(
        task_name="test_task",
        task_source_dir=source_dir,
        task_runtime_dir=runtime_dir,
        task_prompt_file=prompt_file,
    )

    assert get_task_name() == "test_task"
    assert get_task_source_dir() == source_dir.resolve()
    assert get_task_runtime_dir() == runtime_dir.resolve()
    assert get_task_prompt_file() == prompt_file.resolve()


def test_get_task_name_default(monkeypatch):
    monkeypatch.delenv(TASK_NAME_ENV, raising=False)
    assert get_task_name() == ""
    assert get_task_name(default="fallback") == "fallback"


def test_get_task_runtime_dir_not_set_raises(monkeypatch):
    monkeypatch.delenv(TASK_RUNTIME_DIR_ENV, raising=False)
    with pytest.raises(RuntimeError):
        get_task_runtime_dir()


def test_get_task_runtime_dir_with_default(tmp_path, monkeypatch):
    monkeypatch.delenv(TASK_RUNTIME_DIR_ENV, raising=False)
    result = get_task_runtime_dir(default=tmp_path)
    assert result == tmp_path.resolve()


def test_get_task_source_dir_not_set_raises(monkeypatch):
    monkeypatch.delenv(TASK_SOURCE_DIR_ENV, raising=False)
    with pytest.raises(RuntimeError):
        get_task_source_dir()


def test_get_task_prompt_file_not_set_raises(monkeypatch):
    monkeypatch.delenv(TASK_PROMPT_FILE_ENV, raising=False)
    with pytest.raises(RuntimeError):
        get_task_prompt_file()


# ---------------------------------------------------------------------------
# timezone
# ---------------------------------------------------------------------------

def test_set_and_get_timezone_name(monkeypatch):
    monkeypatch.delenv(TIMEZONE_ENV, raising=False)
    set_timezone_name("utc")
    assert get_timezone_name() == "utc"


def test_get_timezone_name_default(monkeypatch):
    monkeypatch.delenv(TIMEZONE_ENV, raising=False)
    assert get_timezone_name() == "shanghai"
    assert get_timezone_name(default="utc") == "utc"


def test_get_timezone_name_empty_env_uses_default(monkeypatch):
    monkeypatch.setenv(TIMEZONE_ENV, "")
    assert get_timezone_name(default="beijing") == "beijing"
