from __future__ import annotations

class AgentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def build_error(code: str, message: str) -> AgentError:
    return AgentError(code=code, message=message)


class ConfigError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(code="CONFIG_ERROR", message=message)
