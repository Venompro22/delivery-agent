"""📜 Tests for the security log."""
import time

import pytest

import audit as audit_module
import fileapp
from audit import AuditLog


@pytest.fixture
def log(tmp_path):
    return AuditLog(str(tmp_path / "test.db"))


def test_newest_first_and_size_capped(log):
    log.add("login", "1.2.3.4", "owner")
    log.add("login_failed", "5.6.7.8", "x" * 500)       # a stranger typed 500 characters
    events = log.recent()
    assert [e["event"] for e in events] == ["login_failed", "login"]
    assert len(events[0]["who"]) == 40


def test_old_events_are_deleted(log, monkeypatch):
    log.add("login", "1.2.3.4")
    later = time.time() + 91 * 86400                     # 91 days later
    monkeypatch.setattr(audit_module.time, "time", lambda: later)
    log.add("login", "5.6.7.8")
    assert [e["ip"] for e in log.recent()] == ["5.6.7.8"]


def test_wrong_password_is_logged(client):
    client.post("/login", data={"password": "nope"})
    assert fileapp.audit.recent()[0]["event"] == "login_failed"
