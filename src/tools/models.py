from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from schemas import AgentError, ToolResult


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    def can_handle(self, tool_name: str) -> bool:
        return self.name == tool_name

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def reset(self) -> None:
        """Called between tasks to clear any per-task state. Override if needed."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def build_tool_output(
    *,
    success: bool,
    data: dict[str, Any] | None = None,
    error: AgentError | None = None,
) -> str:
    payload = {
        "success": success,
        "data": data,
        "error": None,
    }
    if error is not None:
        payload["error"] = {
            "code": error.code,
            "message": error.message,
        }
    return json.dumps(payload, ensure_ascii=False)
