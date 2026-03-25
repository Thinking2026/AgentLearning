from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from schemas import ToolResult


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
