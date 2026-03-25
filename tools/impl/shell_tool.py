from __future__ import annotations

import subprocess
from pathlib import Path

from schemas import ToolResult, build_error
from tools.tools import BaseTool


class ShellTool(BaseTool):
    name = "shell"
    description = "Run a shell command and return stdout or stderr."
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds.",
                "default": 15,
            },
        },
        "required": ["command"],
    }

    def run(self, arguments: dict[str, object]) -> ToolResult:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("TOOL_ARGUMENT_ERROR", "Shell tool requires a non-empty command."),
            )

        timeout = int(arguments.get("timeout", 15))
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=Path.cwd(),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("SHELL_TIMEOUT", f"Shell command timed out after {timeout} seconds."),
            )
        except Exception as exc:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("SHELL_EXECUTION_ERROR", f"Shell command failed to start: {exc}"),
            )

        output = completed.stdout.strip()
        error_output = completed.stderr.strip()
        if completed.returncode != 0:
            message = error_output or output or f"Command exited with code {completed.returncode}"
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("SHELL_COMMAND_FAILED", message),
            )

        return ToolResult(call_id="", output=output or "(no output)", success=True)
