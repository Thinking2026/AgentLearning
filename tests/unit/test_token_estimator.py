from __future__ import annotations

import json

import pytest

from agent.models.context.estimator.token_estimator import (
    ClaudeTokenEstimator,
    TokenEstimatorFactory,
    _estimate_by_role,
)
from schemas.types import LLMMessage, UnifiedLLMRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request(
    user_msgs: list[str] | None = None,
    assistant_msgs: list[str] | None = None,
    tool_msgs: list[str] | None = None,
    system_prompt: str | None = None,
    tools: list | None = None,
) -> UnifiedLLMRequest:
    messages: list[LLMMessage] = []
    for content in user_msgs or []:
        messages.append(LLMMessage(role="user", content=content))
    for content in assistant_msgs or []:
        messages.append(LLMMessage(role="assistant", content=content))
    for content in tool_msgs or []:
        messages.append(LLMMessage(role="tool", content=content, metadata={"tool_name": "t", "llm_raw_tool_call_id": "id1"}))
    return UnifiedLLMRequest(messages=messages, system_prompt=system_prompt, tool_schemas=tools)


# ---------------------------------------------------------------------------
# ClaudeTokenEstimator
# ---------------------------------------------------------------------------

def test_claude_estimator_empty_request():
    est = ClaudeTokenEstimator()
    req = UnifiedLLMRequest(messages=[])
    result = est.estimate(req)
    assert result["total"] == 0


def test_claude_estimator_user_tokens():
    est = ClaudeTokenEstimator()
    # 35 chars / 3.5 = 10 tokens
    req = make_request(user_msgs=["a" * 35])
    result = est.estimate(req, roles=["user"])
    assert result["user"] == 10
    assert result["total"] == 10


def test_claude_estimator_assistant_tokens():
    est = ClaudeTokenEstimator()
    req = make_request(assistant_msgs=["b" * 70])
    result = est.estimate(req, roles=["assistant"])
    assert result["assistant"] == 20


def test_claude_estimator_tool_tokens():
    est = ClaudeTokenEstimator()
    req = make_request(tool_msgs=["c" * 35])
    result = est.estimate(req, roles=["tool"])
    # Each field counted separately: int(35/3.5)=10, int(1/3.5)=0, int(3/3.5)=0 → total 10
    assert result["tool"] == 10


def test_claude_estimator_system_tokens():
    est = ClaudeTokenEstimator()
    req = make_request(system_prompt="s" * 35)
    result = est.estimate(req, roles=["system"])
    assert result["system"] == 10


def test_claude_estimator_system_with_tools():
    est = ClaudeTokenEstimator()
    tools = [{"name": "calc"}]
    req = make_request(system_prompt="s" * 35, tools=tools)
    result = est.estimate(req, roles=["system"])
    tools_json = json.dumps(tools)
    expected = int((35 + len(tools_json)) / 3.5)
    assert result["system"] == expected


def test_claude_estimator_all_roles():
    est = ClaudeTokenEstimator()
    req = make_request(user_msgs=["u" * 35], assistant_msgs=["a" * 35])
    result = est.estimate(req)
    assert "user" in result
    assert "assistant" in result
    assert "tool" in result
    assert "system" in result
    assert result["total"] == result["user"] + result["assistant"] + result["tool"] + result["system"]


def test_claude_estimator_single_role_string():
    est = ClaudeTokenEstimator()
    req = make_request(user_msgs=["u" * 35])
    result = est.estimate(req, roles="user")
    assert result["user"] == 10
    assert result["total"] == 10


def test_claude_estimator_assistant_with_tool_calls():
    est = ClaudeTokenEstimator()
    msg = LLMMessage(
        role="assistant",
        content="",
        metadata={
            "tool_calls": [
                {"name": "calc", "arguments": {"expr": "1+1"}, "llm_raw_tool_call_id": "tc1"}
            ]
        },
    )
    req = UnifiedLLMRequest(messages=[msg])
    result = est.estimate(req, roles=["assistant"])
    # Should count name + id + args
    assert result["assistant"] > 0


# ---------------------------------------------------------------------------
# TokenEstimatorFactory
# ---------------------------------------------------------------------------

def test_factory_returns_claude_estimator():
    est = TokenEstimatorFactory.get_estimator("claude")
    assert isinstance(est, ClaudeTokenEstimator)


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        TokenEstimatorFactory.get_estimator("unknown_provider")


def test_factory_openai_returns_estimator():
    # OpenAICompatibleTokenEstimator requires tiktoken; skip if not installed
    pytest.importorskip("tiktoken")
    est = TokenEstimatorFactory.get_estimator("openai")
    assert est is not None


# ---------------------------------------------------------------------------
# _estimate_by_role — unknown role
# ---------------------------------------------------------------------------

def test_estimate_by_role_unknown_raises():
    req = UnifiedLLMRequest(messages=[])
    with pytest.raises(ValueError, match="Unknown role"):
        _estimate_by_role(req, lambda t: len(t), "unknown_role")
