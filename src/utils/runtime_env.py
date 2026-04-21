from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT_ENV = "NANOAGENT_PROJECT_ROOT"
TASK_NAME_ENV = "NANOAGENT_TASK_NAME"
TASK_SOURCE_DIR_ENV = "NANOAGENT_TASK_SOURCE_DIR"
TASK_RUNTIME_DIR_ENV = "NANOAGENT_TASK_RUNTIME_DIR"
TASK_PROMPT_FILE_ENV = "NANOAGENT_TASK_PROMPT_FILE"
TIMEZONE_ENV = "NANOAGENT_TIMEZONE"


def set_project_root(project_root: str | Path) -> Path:
    resolved = Path(project_root).expanduser().resolve()
    os.environ[PROJECT_ROOT_ENV] = str(resolved)
    return resolved


def get_project_root() -> Path:
    raw_value = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    if not raw_value:
        raise RuntimeError(
            "Runtime project root is not initialized. "
            f"Expected environment variable `{PROJECT_ROOT_ENV}` to be set."
        )
    return Path(raw_value).expanduser().resolve()


def set_task_environment(
    *,
    task_name: str,
    task_source_dir: str | Path,
    task_runtime_dir: str | Path,
    task_prompt_file: str | Path,
) -> None:
    os.environ[TASK_NAME_ENV] = task_name
    os.environ[TASK_SOURCE_DIR_ENV] = str(Path(task_source_dir).expanduser().resolve())
    os.environ[TASK_RUNTIME_DIR_ENV] = str(Path(task_runtime_dir).expanduser().resolve())
    os.environ[TASK_PROMPT_FILE_ENV] = str(Path(task_prompt_file).expanduser().resolve())


def get_task_name(default: str = "") -> str:
    return os.environ.get(TASK_NAME_ENV, default).strip()


def get_task_source_dir(default: str | Path | None = None) -> Path:
    return _get_path_from_env(TASK_SOURCE_DIR_ENV, default)


def get_task_runtime_dir(default: str | Path | None = None) -> Path:
    return _get_path_from_env(TASK_RUNTIME_DIR_ENV, default)


def get_task_prompt_file(default: str | Path | None = None) -> Path:
    return _get_path_from_env(TASK_PROMPT_FILE_ENV, default)


def set_timezone_name(timezone_name: str) -> None:
    os.environ[TIMEZONE_ENV] = timezone_name


def get_timezone_name(default: str = "shanghai") -> str:
    return os.environ.get(TIMEZONE_ENV, default).strip() or default


def _get_path_from_env(env_key: str, default: str | Path | None) -> Path:
    raw_value = os.environ.get(env_key, "").strip()
    if raw_value:
        return Path(raw_value).expanduser().resolve()
    if default is None:
        raise RuntimeError(
            f"Runtime path environment variable `{env_key}` is not initialized."
        )
    return Path(default).expanduser().resolve()
