from __future__ import annotations

import threading
import time

import pytest

from schemas.types import UIMessage
from utils.concurrency.message_queue import (
    UserToAgentQueue,
    AgentToUserQueue,
    _BaseMessageQueue,
)


def make_msg(content: str = "hello") -> UIMessage:
    return UIMessage(role="user", content=content)


# ---------------------------------------------------------------------------
# Basic send/get
# ---------------------------------------------------------------------------

def test_send_and_get():
    q = UserToAgentQueue()
    msg = make_msg("test")
    q.send_user_message(msg)
    received = q.get_user_message()
    assert received is msg


def test_get_returns_none_when_closed():
    q = UserToAgentQueue()
    q.close()
    result = q.get_user_message()
    assert result is None


def test_fifo_order():
    q = UserToAgentQueue()
    msgs = [make_msg(f"msg{i}") for i in range(5)]
    for m in msgs:
        q.send_user_message(m)
    received = [q.get_user_message() for _ in range(5)]
    assert [m.content for m in received] == [f"msg{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# AgentToUserQueue
# ---------------------------------------------------------------------------

def test_agent_to_user_queue():
    q = AgentToUserQueue()
    msg = UIMessage(role="assistant", content="response")
    q.send_agent_message(msg)
    received = q.get_agent_message()
    assert received is msg


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_get_with_timeout_returns_none():
    q = UserToAgentQueue()
    result = q.get_user_message(timeout=0.05)
    assert result is None


def test_get_with_timeout_receives_message():
    q = UserToAgentQueue()
    msg = make_msg("delayed")

    def send_later():
        time.sleep(0.02)
        q.send_user_message(msg)

    t = threading.Thread(target=send_later)
    t.start()
    result = q.get_user_message(timeout=1.0)
    t.join()
    assert result is msg


def test_invalid_timeout_raises():
    q = UserToAgentQueue()
    with pytest.raises(ValueError, match="greater than 0"):
        q.get_user_message(timeout=0)


def test_negative_timeout_raises():
    q = UserToAgentQueue()
    with pytest.raises(ValueError, match="greater than 0"):
        q.get_user_message(timeout=-1)


# ---------------------------------------------------------------------------
# close / release
# ---------------------------------------------------------------------------

def test_send_after_close_is_ignored():
    q = UserToAgentQueue()
    q.close()
    q.send_user_message(make_msg("ignored"))
    result = q.get_user_message()
    assert result is None


def test_is_closed():
    q = UserToAgentQueue()
    assert not q.is_closed()
    q.close()
    assert q.is_closed()


def test_release_clears_queue():
    q = UserToAgentQueue()
    q.send_user_message(make_msg("msg"))
    q.release()
    assert q.is_closed()
    result = q.get_user_message()
    assert result is None


# ---------------------------------------------------------------------------
# Blocking get unblocked by close
# ---------------------------------------------------------------------------

def test_blocking_get_unblocked_by_close():
    q = UserToAgentQueue()
    result_holder = []

    def blocking_get():
        result_holder.append(q.get_user_message())

    t = threading.Thread(target=blocking_get)
    t.start()
    time.sleep(0.02)
    q.close()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert result_holder[0] is None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

def test_concurrent_send_and_get():
    q = UserToAgentQueue()
    sent = []
    received = []
    lock = threading.Lock()

    def producer():
        for i in range(50):
            msg = make_msg(f"msg{i}")
            with lock:
                sent.append(msg)
            q.send_user_message(msg)

    def consumer():
        for _ in range(50):
            msg = q.get_user_message(timeout=2.0)
            if msg:
                received.append(msg)

    p = threading.Thread(target=producer)
    c = threading.Thread(target=consumer)
    p.start()
    c.start()
    p.join()
    c.join()

    assert len(received) == 50
