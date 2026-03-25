from __future__ import annotations

from datetime import datetime

from schemas import ToolResult
from tools.tools import BaseTool


class CurrentTimeTool(BaseTool):
    name = "current_time"
    description = "Return the current local time for the running environment."
    parameters = {"type": "object", "properties": {}, "required": []}

    def run(self, arguments: dict[str, object]) -> ToolResult:
        current_time = datetime.now().isoformat(timespec="seconds")
        return ToolResult(call_id="", output=current_time, success=True)
