from __future__ import annotations

from pathlib import Path

from schemas import ToolResult, build_error
from tools.tools import BaseTool


class FileTool(BaseTool):
    name = "file"
    description = "Read, write, or append file content."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of read, write, append.",
                "enum": ["read", "write", "append"],
            },
            "path": {
                "type": "string",
                "description": "Target file path.",
            },
            "content": {
                "type": "string",
                "description": "Content used for write or append.",
            },
        },
        "required": ["action", "path"],
    }

    def run(self, arguments: dict[str, object]) -> ToolResult:
        action = str(arguments.get("action", "")).strip().lower()
        path_value = str(arguments.get("path", "")).strip()
        content = str(arguments.get("content", ""))

        if action not in {"read", "write", "append"}:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("TOOL_ARGUMENT_ERROR", "File tool action must be read, write, or append."),
            )
        if not path_value:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("TOOL_ARGUMENT_ERROR", "File tool requires a non-empty path."),
            )

        target_path = Path(path_value).expanduser()
        try:
            if action == "read":
                return ToolResult(
                    call_id="",
                    output=target_path.read_text(encoding="utf-8"),
                    success=True,
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            if action == "write":
                target_path.write_text(content, encoding="utf-8")
                return ToolResult(call_id="", output=f"Wrote file: {target_path}", success=True)

            with target_path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(content)
            return ToolResult(call_id="", output=f"Appended file: {target_path}", success=True)
        except Exception as exc:
            return ToolResult(
                call_id="",
                output="",
                success=False,
                error=build_error("FILE_TOOL_ERROR", f"File tool failed: {exc}"),
            )
