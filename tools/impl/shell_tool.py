from __future__ import annotations

import subprocess
from pathlib import Path

from schemas import (
    SHELL_COMMAND_FAILED,
    SHELL_EXECUTION_ERROR,
    SHELL_TIMEOUT,
    TOOL_ARGUMENT_ERROR,
    ToolResult,
    build_error,
)
from tools.tools import BaseTool, build_tool_output
from utils.runtime_env import get_project_root, get_task_runtime_dir


class ShellTool(BaseTool):
    name = "shell"
    description = (
        "Run a shell command and return its output. "
        "Commands execute in the task workspace directory (not the project root). "
        "The command is run via the system shell (shell=True), so pipes, redirects, and "
        "environment variables work as expected. "
        "If the command exits with a non-zero return code the call is treated as a failure; "
        "stderr is used as the error message when available. "
        "Long-running or interactive commands should be avoided; use the timeout parameter to cap execution time."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The shell command to execute. "
                    "Runs in the task workspace directory. "
                    "Non-zero exit code is treated as failure."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Maximum seconds to wait before killing the command. "
                    "Defaults to 15. Increase for commands known to be slow."
                ),
                "default": 15,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, object]) -> ToolResult:
        command = str(arguments.get("command", "")).strip()
        if not command:
            error = build_error(TOOL_ARGUMENT_ERROR, "Shell tool requires a non-empty command.")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )

        timeout = int(arguments.get("timeout", 15))
        working_directory = self._working_directory()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=working_directory,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            error = build_error(SHELL_TIMEOUT, f"Shell command timed out after {timeout} seconds.")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )
        except Exception as exc:
            error = build_error(SHELL_EXECUTION_ERROR, f"Shell command failed to start: {exc}")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )

        output = completed.stdout.strip()
        error_output = completed.stderr.strip()
        if completed.returncode != 0:
            message = error_output or output or f"Command exited with code {completed.returncode}"
            error = build_error(SHELL_COMMAND_FAILED, message)
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )

        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "command": command,
                    "cwd": str(working_directory),
                    "stdout": output,
                    "stderr": error_output,
                    "exit_code": completed.returncode,
                },
            ),
            success=True,
        )

    @staticmethod
    def _working_directory() -> Path:
        try:
            path = get_task_runtime_dir()
            path.mkdir(parents=True, exist_ok=True)
            return path
        except RuntimeError:
            return get_project_root()
