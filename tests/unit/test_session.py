from __future__ import annotations

import threading
import time

import pytest

from agent.session import Session
from schemas.consts import SessionStatus


def test_default_status_is_new_task():
    s = Session()
    assert s.get_status() == SessionStatus.NEW_TASK


def test_begin_sets_in_progress():
    s = Session()
    s.begin()
    assert s.get_status() == SessionStatus.IN_PROGRESS


def test_reset_sets_new_task():
    s = Session()
    s.begin()
    s.reset()
    assert s.get_status() == SessionStatus.NEW_TASK


def test_set_status():
    s = Session(status=SessionStatus.IN_PROGRESS)
    assert s.get_status() == SessionStatus.IN_PROGRESS
    s.set_status(SessionStatus.NEW_TASK)
    assert s.get_status() == SessionStatus.NEW_TASK


def test_custom_initial_status():
    s = Session(status=SessionStatus.IN_PROGRESS)
    assert s.get_status() == SessionStatus.IN_PROGRESS


def test_thread_safe_status_changes():
    s = Session()
    errors = []

    def toggle():
        try:
            for _ in range(100):
                s.begin()
                s.reset()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=toggle) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert s.get_status() in (SessionStatus.NEW_TASK, SessionStatus.IN_PROGRESS)
