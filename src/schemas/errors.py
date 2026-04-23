from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.types import LLMRequest

# ---------------------------------------------------------------------------
# Structured LLM error hierarchy (level-1 category + level-2 code)
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    TRANSIENT  = "TRANSIENT"   # network / timeout / 5xx → retry same provider
    RATE_LIMIT = "RATE_LIMIT"  # 429 → retry same provider with backoff
    CONTEXT    = "CONTEXT"     # context too long → trim and retry
    AUTH       = "AUTH"        # 401/403 → skip to next provider
    RESPONSE   = "RESPONSE"    # bad/unparseable response → self-repair then skip
    CONFIG     = "CONFIG"      # missing key / bad config → skip to next provider


class LLMErrorCode(str, Enum):
    NETWORK_ERROR        = "NETWORK_ERROR"
    TIMEOUT              = "TIMEOUT"
    HTTP_5XX             = "HTTP_5XX"
    RATE_LIMITED         = "RATE_LIMITED"
    CONTEXT_TOO_LONG     = "CONTEXT_TOO_LONG"
    AUTH_FAILED          = "AUTH_FAILED"
    RESPONSE_ERROR       = "RESPONSE_ERROR"
    RESPONSE_PARSE_ERROR = "RESPONSE_PARSE_ERROR"
    CONFIG_ERROR         = "CONFIG_ERROR"


_CODE_CATEGORY: dict[LLMErrorCode, ErrorCategory] = {
    LLMErrorCode.NETWORK_ERROR:        ErrorCategory.TRANSIENT,
    LLMErrorCode.TIMEOUT:              ErrorCategory.TRANSIENT,
    LLMErrorCode.HTTP_5XX:             ErrorCategory.TRANSIENT,
    LLMErrorCode.RATE_LIMITED:         ErrorCategory.RATE_LIMIT,
    LLMErrorCode.CONTEXT_TOO_LONG:     ErrorCategory.CONTEXT,
    LLMErrorCode.AUTH_FAILED:          ErrorCategory.AUTH,
    LLMErrorCode.RESPONSE_ERROR:       ErrorCategory.RESPONSE,
    LLMErrorCode.RESPONSE_PARSE_ERROR: ErrorCategory.RESPONSE,
    LLMErrorCode.CONFIG_ERROR:         ErrorCategory.CONFIG,
}


class LLMError(Exception):
    """Structured LLM error with a level-1 category and level-2 code.

    Raised by concrete providers; AgentExecutor inspects .category to decide
    whether to retry the same provider, trim context, self-repair, or skip.
    """

    def __init__(self, code: LLMErrorCode, message: str, retry_after: float | None = None) -> None:
        self.code = code
        self.category: ErrorCategory = _CODE_CATEGORY[code]
        self.message = message
        self.retry_after = retry_after
        super().__init__(f"[{self.category.value}/{self.code.value}] {message}")


# ---------------------------------------------------------------------------
# Legacy flat error codes (kept for tools, storage, and agent-level code)
# ---------------------------------------------------------------------------

AGENT_EXECUTION_ERROR = "AGENT_EXECUTION_ERROR"
AGENT_MAX_ITERATIONS_EXCEEDED = "AGENT_MAX_ITERATIONS_EXCEEDED"
AGENT_STRATEGY_NOT_FOUND = "AGENT_STRATEGY_NOT_FOUND"
AGENT_THREAD_ERROR = "AGENT_THREAD_ERROR"
CALCULATION_ERROR = "CALCULATION_ERROR"
CONFIG_ERROR = "CONFIG_ERROR"
EXCEL_TOOL_ERROR = "EXCEL_TOOL_ERROR"
EXCEL_TOOL_DEPENDENCY_ERROR = "EXCEL_TOOL_DEPENDENCY_ERROR"
EXCEL_TOOL_FILE_NOT_FOUND = "EXCEL_TOOL_FILE_NOT_FOUND"
EXCEL_TOOL_SHEET_EXISTS = "EXCEL_TOOL_SHEET_EXISTS"
EXCEL_TOOL_SHEET_NOT_FOUND = "EXCEL_TOOL_SHEET_NOT_FOUND"
FILE_TOOL_ERROR = "FILE_TOOL_ERROR"
LLM_ALL_PROVIDERS_FAILED = "LLM_ALL_PROVIDERS_FAILED"
LLM_RESPONSE_TRUNCATED = "LLM_RESPONSE_TRUNCATED"
LLM_CONFIG_ERROR = "LLM_CONFIG_ERROR"
LLM_CONTEXT_TOO_LONG = "LLM_CONTEXT_TOO_LONG"
LLM_HTTP_ERROR = "LLM_HTTP_ERROR"
LLM_NETWORK_ERROR = "LLM_NETWORK_ERROR"
LLM_PROVIDER_NOT_FOUND = "LLM_PROVIDER_NOT_FOUND"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
LLM_RESPONSE_ERROR = "LLM_RESPONSE_ERROR"
LLM_RESPONSE_PARSE_ERROR = "LLM_RESPONSE_PARSE_ERROR"
LLM_TIMEOUT = "LLM_TIMEOUT"
SHELL_COMMAND_FAILED = "SHELL_COMMAND_FAILED"
SHELL_EXECUTION_ERROR = "SHELL_EXECUTION_ERROR"
SHELL_TIMEOUT = "SHELL_TIMEOUT"
SQL_QUERY_TOOL_ERROR = "SQL_QUERY_TOOL_ERROR"
SQL_SCHEMA_TOOL_ERROR = "SQL_SCHEMA_TOOL_ERROR"
STORAGE_CONFIG_ERROR = "STORAGE_CONFIG_ERROR"
STORAGE_DEPENDENCY_ERROR = "STORAGE_DEPENDENCY_ERROR"
STORAGE_QUERY_ERROR = "STORAGE_QUERY_ERROR"
STORAGE_RESOURCE_NOT_FOUND = "STORAGE_RESOURCE_NOT_FOUND"
STORAGE_RESOURCE_REQUIRED = "STORAGE_RESOURCE_REQUIRED"
TOOL_ARGUMENT_ERROR = "TOOL_ARGUMENT_ERROR"
TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
PYTHON_TOOL_ERROR = "PYTHON_TOOL_ERROR"
PYTHON_TOOL_FORBIDDEN_IMPORT = "PYTHON_TOOL_FORBIDDEN_IMPORT"
PYTHON_TOOL_TIMEOUT = "PYTHON_TOOL_TIMEOUT"
PYTHON_TOOL_RESOURCE_LIMIT = "PYTHON_TOOL_RESOURCE_LIMIT"
SEARCH_TOOL_ERROR = "SEARCH_TOOL_ERROR"
SEARCH_TOOL_TIMEOUT = "SEARCH_TOOL_TIMEOUT"
SEARCH_TOOL_PROVIDER_ERROR = "SEARCH_TOOL_PROVIDER_ERROR"
VECTOR_SCHEMA_TOOL_ERROR = "VECTOR_SCHEMA_TOOL_ERROR"
VECTOR_SEARCH_TOOL_ERROR = "VECTOR_SEARCH_TOOL_ERROR"


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
        super().__init__(code=CONFIG_ERROR, message=message)


class HttpError(Exception):
    """Raised for HTTP error responses; carries status code and Retry-After."""

    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        self.status = status
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {body}")

class ProviderFailure(Exception):
    """Raised by SingleProviderClient when this provider cannot serve the request.

    Carries the final request state (possibly trimmed) so the fallback layer
    can pass it to the next provider unchanged.
    """

    def __init__(self, provider_name: str, message: str, final_request: "LLMRequest") -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.final_request = final_request
