from __future__ import annotations

import pytest

from schemas.types import (
    UIMessage,
    LLMMessage,
    ToolCall,
    ToolResult,
    LLMRequest,
    LLMResponse,
    AgentExecutionResult,
    RoleBudget,
    BudgetResult,
)
from schemas.errors import (
    ErrorCategory,
    LLMErrorCode,
    LLMError,
    AgentError,
    ConfigError,
    HttpError,
    ProviderFailure,
    build_error,
    CONFIG_ERROR,
    TOOL_NOT_FOUND,
)
from schemas.message_convert import ui_to_llm, llm_to_ui


# ---------------------------------------------------------------------------
# UIMessage / LLMMessage
# ---------------------------------------------------------------------------

def test_ui_message_defaults():
    msg = UIMessage(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"
    assert msg.metadata == {}


def test_ui_message_with_metadata():
    msg = UIMessage(role="assistant", content="hi", metadata={"key": "val"})
    assert msg.metadata["key"] == "val"


def test_llm_message_defaults():
    msg = LLMMessage(role="tool", content="result")
    assert msg.role == "tool"
    assert msg.metadata == {}


def test_llm_message_metadata_independent():
    msg1 = LLMMessage(role="user", content="a")
    msg2 = LLMMessage(role="user", content="b")
    msg1.metadata["x"] = 1
    assert "x" not in msg2.metadata


# ---------------------------------------------------------------------------
# ToolCall / ToolResult
# ---------------------------------------------------------------------------

def test_tool_call_defaults():
    tc = ToolCall(name="calc", arguments={"expr": "1+1"})
    assert tc.llm_raw_tool_call_id is None


def test_tool_call_with_id():
    tc = ToolCall(name="calc", arguments={}, llm_raw_tool_call_id="tc_1")
    assert tc.llm_raw_tool_call_id == "tc_1"


def test_tool_result_defaults():
    tr = ToolResult(output="ok")
    assert tr.success is True
    assert tr.error is None
    assert tr.llm_raw_tool_call_id is None


def test_tool_result_failure():
    err = AgentError(code="ERR", message="bad")
    tr = ToolResult(output="", success=False, error=err)
    assert not tr.success
    assert tr.error is err


# ---------------------------------------------------------------------------
# LLMRequest / LLMResponse
# ---------------------------------------------------------------------------

def test_llm_request_defaults():
    req = LLMRequest(messages=[])
    assert req.system_prompt is None
    assert req.tools is None


def test_llm_response_defaults():
    msg = LLMMessage(role="assistant", content="done")
    resp = LLMResponse(assistant_message=msg)
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"
    assert resp.raw_response == {}


# ---------------------------------------------------------------------------
# AgentExecutionResult / RoleBudget / BudgetResult
# ---------------------------------------------------------------------------

def test_agent_execution_result_defaults():
    r = AgentExecutionResult()
    assert r.user_messages == []
    assert r.error is None
    assert r.task_completed is False


def test_role_budget():
    rb = RoleBudget(role="assistant", ratio=0.3, token_budget=300)
    assert rb.role == "assistant"
    assert rb.ratio == pytest.approx(0.3)
    assert rb.token_budget == 300


def test_budget_result():
    br = BudgetResult(
        strategy="react",
        total_budget=1000,
        reserve_ratio=0.2,
        reserved_tokens=200,
        available_tokens=800,
    )
    assert br.role_budgets == {}


# ---------------------------------------------------------------------------
# LLMError
# ---------------------------------------------------------------------------

def test_llm_error_category_mapping():
    cases = [
        (LLMErrorCode.NETWORK_ERROR, ErrorCategory.TRANSIENT),
        (LLMErrorCode.TIMEOUT, ErrorCategory.TRANSIENT),
        (LLMErrorCode.HTTP_5XX, ErrorCategory.TRANSIENT),
        (LLMErrorCode.RATE_LIMITED, ErrorCategory.RATE_LIMIT),
        (LLMErrorCode.CONTEXT_TOO_LONG, ErrorCategory.CONTEXT),
        (LLMErrorCode.AUTH_FAILED, ErrorCategory.AUTH),
        (LLMErrorCode.RESPONSE_ERROR, ErrorCategory.RESPONSE),
        (LLMErrorCode.RESPONSE_PARSE_ERROR, ErrorCategory.RESPONSE),
        (LLMErrorCode.CONFIG_ERROR, ErrorCategory.CONFIG),
    ]
    for code, expected_category in cases:
        err = LLMError(code, "msg")
        assert err.category == expected_category, f"Failed for {code}"


def test_llm_error_str_contains_category_and_code():
    err = LLMError(LLMErrorCode.RATE_LIMITED, "too many requests")
    assert "RATE_LIMIT" in str(err)
    assert "RATE_LIMITED" in str(err)


def test_llm_error_retry_after():
    err = LLMError(LLMErrorCode.RATE_LIMITED, "slow down", retry_after=30.0)
    assert err.retry_after == pytest.approx(30.0)


def test_llm_error_is_exception():
    err = LLMError(LLMErrorCode.TIMEOUT, "timed out")
    with pytest.raises(LLMError):
        raise err


# ---------------------------------------------------------------------------
# AgentError / ConfigError / build_error
# ---------------------------------------------------------------------------

def test_agent_error_str():
    err = AgentError(code="MY_CODE", message="something went wrong")
    assert "[MY_CODE]" in str(err)
    assert "something went wrong" in str(err)


def test_build_error_returns_agent_error():
    err = build_error(TOOL_NOT_FOUND, "tool missing")
    assert isinstance(err, AgentError)
    assert err.code == TOOL_NOT_FOUND


def test_config_error_code():
    err = ConfigError("bad config")
    assert err.code == CONFIG_ERROR
    assert "bad config" in err.message


# ---------------------------------------------------------------------------
# HttpError
# ---------------------------------------------------------------------------

def test_http_error_attributes():
    err = HttpError(status=429, body="rate limited", retry_after=5.0)
    assert err.status == 429
    assert err.body == "rate limited"
    assert err.retry_after == pytest.approx(5.0)


def test_http_error_str():
    err = HttpError(status=500, body="server error")
    assert "500" in str(err)


def test_http_error_no_retry_after():
    err = HttpError(status=401, body="unauthorized")
    assert err.retry_after is None


# ---------------------------------------------------------------------------
# ProviderFailure
# ---------------------------------------------------------------------------

def test_provider_failure():
    req = LLMRequest(messages=[])
    err = ProviderFailure(provider_name="openai", message="failed", final_request=req)
    assert err.provider_name == "openai"
    assert err.final_request is req


# ---------------------------------------------------------------------------
# message_convert
# ---------------------------------------------------------------------------

def test_ui_to_llm_user():
    ui = UIMessage(role="user", content="hello")
    llm = ui_to_llm(ui)
    assert llm.role == "user"
    assert llm.content == "hello"


def test_ui_to_llm_assistant():
    ui = UIMessage(role="assistant", content="hi")
    llm = ui_to_llm(ui)
    assert llm.role == "assistant"


def test_llm_to_ui_assistant():
    llm = LLMMessage(role="assistant", content="answer")
    ui = llm_to_ui(llm)
    assert ui.role == "assistant"
    assert ui.content == "answer"


def test_llm_to_ui_tool_becomes_assistant():
    llm = LLMMessage(role="tool", content="result")
    ui = llm_to_ui(llm)
    assert ui.role == "assistant"


def test_llm_to_ui_user():
    llm = LLMMessage(role="user", content="question")
    ui = llm_to_ui(llm)
    assert ui.role == "user"
