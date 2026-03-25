from __future__ import annotations

import importlib
import inspect
import pkgutil
import subprocess
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from schemas import ToolCall, ToolResult, build_error


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    def can_handle(self, tool_name: str) -> bool:
        return self.name == tool_name

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


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

    def run(self, arguments: dict[str, Any]) -> ToolResult:
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


class CurrentTimeTool(BaseTool):
    name = "current_time"
    description = "Return the current local time for the running environment."
    parameters = {"type": "object", "properties": {}, "required": []}

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        current_time = datetime.now().isoformat(timespec="seconds")
        return ToolResult(call_id="", output=current_time, success=True)


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

    def run(self, arguments: dict[str, Any]) -> ToolResult:
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


class ToolRegistry:
    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools = {tool.name: tool for tool in (tools or [])}
        self._router = ToolChainRouter(self._tools.values())

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        self._router = ToolChainRouter(self._tools.values())

    def auto_register(
        self,
        module_names: list[str] | None = None,
        package_name: str | None = None,
    ) -> None:
        for tool in discover_tools(module_names=module_names, package_name=package_name):
            self.register(tool)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any], call_id: str) -> ToolResult:
        return self._router.route(ToolCall(name=name, arguments=arguments, call_id=call_id))


class BaseToolHandler(ABC):
    def __init__(self) -> None:
        self._next_handler: BaseToolHandler | None = None

    def set_next(self, handler: BaseToolHandler) -> BaseToolHandler:
        self._next_handler = handler
        return handler

    def handle(self, tool_call: ToolCall) -> ToolResult:
        if self.can_handle(tool_call):
            return self.process(tool_call)
        if self._next_handler is not None:
            return self._next_handler.handle(tool_call)
        return ToolResult(
            call_id=tool_call.call_id,
            output="",
            success=False,
            error=build_error("TOOL_NOT_FOUND", f"Unknown tool: {tool_call.name}"),
        )

    @abstractmethod
    def can_handle(self, tool_call: ToolCall) -> bool:
        raise NotImplementedError

    @abstractmethod
    def process(self, tool_call: ToolCall) -> ToolResult:
        raise NotImplementedError


class ToolHandlerNode(BaseToolHandler):
    def __init__(self, tool: BaseTool) -> None:
        super().__init__()
        self._tool = tool

    def can_handle(self, tool_call: ToolCall) -> bool:
        return self._tool.can_handle(tool_call.name)

    def process(self, tool_call: ToolCall) -> ToolResult:
        result = self._tool.run(tool_call.arguments)
        result.call_id = tool_call.call_id
        return result


class FallbackToolHandler(BaseToolHandler):
    def can_handle(self, tool_call: ToolCall) -> bool:
        return True

    def process(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            call_id=tool_call.call_id,
            output="",
            success=False,
            error=build_error("TOOL_NOT_FOUND", f"Unknown tool: {tool_call.name}"),
        )


class ToolChainRouter:
    def __init__(self, tools: list[BaseTool] | Any) -> None:
        self._root_handler = self._build_chain(list(tools))

    def route(self, tool_call: ToolCall) -> ToolResult:
        return self._root_handler.handle(tool_call)

    @staticmethod
    def _build_chain(tools: list[BaseTool]) -> BaseToolHandler:
        fallback = FallbackToolHandler()
        if not tools:
            return fallback

        handlers = [ToolHandlerNode(tool) for tool in tools]
        current = handlers[0]
        root = current
        for handler in handlers[1:]:
            current = current.set_next(handler)
        current.set_next(fallback)
        return root


def discover_tools(
    module_names: list[str] | None = None,
    package_name: str | None = None,
) -> list[BaseTool]:
    discovered_modules = [_safe_import(__name__)]

    for module_name in module_names or []:
        module = _safe_import(module_name)
        if module is not None:
            discovered_modules.append(module)

    if package_name:
        discovered_modules.extend(_discover_package_modules(package_name))

    tools: dict[str, BaseTool] = {}
    for module in discovered_modules:
        if module is None:
            continue
        for _, candidate in inspect.getmembers(module, inspect.isclass):
            if not issubclass(candidate, BaseTool) or candidate is BaseTool:
                continue
            if inspect.isabstract(candidate):
                continue
            try:
                tool = candidate()
            except TypeError:
                continue
            tools[tool.name] = tool
    return list(tools.values())


def create_default_tool_registry(
    module_names: list[str] | None = None,
    package_name: str | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.auto_register(module_names=module_names, package_name=package_name)
    return registry


def _safe_import(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None


def _discover_package_modules(package_name: str) -> list[Any]:
    package = _safe_import(package_name)
    if package is None:
        return []

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return [package]

    modules = [package]
    for module_info in pkgutil.walk_packages(package_path, prefix=f"{package_name}."):
        module = _safe_import(module_info.name)
        if module is not None:
            modules.append(module)
    return modules
