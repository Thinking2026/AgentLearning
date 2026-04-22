from __future__ import annotations

from pathlib import Path

from schemas import FILE_TOOL_ERROR, TOOL_ARGUMENT_ERROR, ToolResult, build_error
from tools.tools import BaseTool, build_tool_output
from utils.env_util.runtime_env import get_task_runtime_dir


class FileTool(BaseTool):
    name = "file"
    description = (
        "Read, write, append a UTF-8 text file, or list a directory. "
        "Supports four actions: "
        "(1) read — return the full file contents as a string; fails if the file does not exist; "
        "only UTF-8 encoded text files are supported, binary files will cause an error; "
        "(2) write — write content to a file, completely overwriting any existing content; "
        "parent directories are created automatically if they do not exist; "
        "(3) append — append content to the end of a file, creating it if it does not exist; "
        "parent directories are created automatically; "
        "(4) list_dir — list the entries in a directory, returning each entry's name, type (file or directory), "
        "and size in bytes (for files); fails if the path does not exist or is not a directory. "
        "Relative paths are resolved inside the current task workspace directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "The file operation to perform. "
                    "read: return file contents (UTF-8 only, file must exist). "
                    "write: overwrite the file with new content (creates file and parent dirs if absent). "
                    "append: add content to the end of the file (creates file and parent dirs if absent). "
                    "list_dir: list entries in a directory (name, type, size)."
                ),
                "enum": ["read", "write", "append", "list_dir"],
            },
            "path": {
                "type": "string",
                "description": (
                    "Path to the target file. "
                    "Relative paths are resolved inside the task workspace directory. "
                    "Absolute paths are used as-is."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The text content to write or append. "
                    "Required when action is write or append. "
                    "Ignored for read."
                ),
            },
        },
        "required": ["action", "path"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, object]) -> ToolResult:
        action = str(arguments.get("action", "")).strip().lower()
        path_value = str(arguments.get("path", "")).strip()
        content = str(arguments.get("content", ""))

        if action not in {"read", "write", "append", "list_dir"}:
            error = build_error(TOOL_ARGUMENT_ERROR, "File tool action must be read, write, append, or list_dir.")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )
        if not path_value:
            error = build_error(TOOL_ARGUMENT_ERROR, "File tool requires a non-empty path.")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )

        target_path = self._resolve_target_path(path_value)
        try:
            if action == "list_dir":
                if not target_path.exists():
                    error = build_error(FILE_TOOL_ERROR, f"File tool list_dir failed: path does not exist: {target_path}")
                    return ToolResult(
                        output=build_tool_output(success=False, error=error),
                        success=False,
                        error=error,
                    )
                if not target_path.is_dir():
                    error = build_error(FILE_TOOL_ERROR, f"File tool list_dir failed: path is not a directory: {target_path}")
                    return ToolResult(
                        output=build_tool_output(success=False, error=error),
                        success=False,
                        error=error,
                    )
                entries = []
                for entry in sorted(target_path.iterdir()):
                    if entry.is_dir():
                        entries.append({"name": entry.name, "type": "directory"})
                    else:
                        entries.append({"name": entry.name, "type": "file", "size": entry.stat().st_size})
                return ToolResult(
                    output=build_tool_output(
                        success=True,
                        data={
                            "action": action,
                            "path": str(target_path),
                            "entry_count": len(entries),
                            "entries": entries,
                        },
                    ),
                    success=True,
                )

            if action == "read":
                content = target_path.read_text(encoding="utf-8")
                return ToolResult(
                    output=build_tool_output(
                        success=True,
                        data={
                            "action": action,
                            "path": str(target_path),
                            "content": content,
                        },
                    ),
                    success=True,
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            if action == "write":
                target_path.write_text(content, encoding="utf-8")
                return ToolResult(
                    output=build_tool_output(
                        success=True,
                        data={
                            "action": action,
                            "path": str(target_path),
                            "bytes_written": len(content.encode("utf-8")),
                        },
                    ),
                    success=True,
                )

            with target_path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(content)
            return ToolResult(
                output=build_tool_output(
                    success=True,
                    data={
                        "action": action,
                        "path": str(target_path),
                        "bytes_written": len(content.encode("utf-8")),
                    },
                ),
                success=True,
            )
        except Exception as exc:
            error = build_error(FILE_TOOL_ERROR, f"File tool failed: {exc}")
            return ToolResult(
                output=build_tool_output(success=False, error=error),
                success=False,
                error=error,
            )

    @staticmethod
    def _resolve_target_path(path_value: str) -> Path:
        target_path = Path(path_value).expanduser()
        if target_path.is_absolute():
            return target_path
        try:
            return get_task_runtime_dir() / target_path
        except RuntimeError:
            pass
        return target_path
