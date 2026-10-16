"""🛡️ Tests for the login limiter."""
import time

import pytest

import limiter as limiter_module
from limiter import LoginLimiter


@pytest.fixture
def lim(tmp_path):
    return LoginLimiter(str(tmp_path / "test.db"))


def test_ip_is_blocked_after_10_failures(lim):
    for _ in range(9):
        lim.fail("1.2.3.4")
    assert not lim.blocked("1.2.3.4")
    lim.fail("1.2.3.4")
    assert lim.blocked("1.2.3.4")
    assert not lim.blocked("5.6.7.8")                 # other people are not affected


def test_phone_is_blocked_after_5_failures_from_any_ip(lim):
    for i in range(5):
        lim.fail(f"10.0.0.{i}", "13800138000")       # 5 different IPs, same phone
    assert lim.blocked("10.0.0.99", "13800138000")
    assert not lim.blocked("10.0.0.99", "13900139000")


def test_success_clears_the_phone(lim):
    for _ in range(4):
        lim.fail("1.2.3.4", "13800138000")
    lim.success("13800138000")
    lim.fail("1.2.3.4", "13800138000")
    assert not lim.blocked("1.2.3.4", "13800138000")


def test_old_failures_are_forgotten(lim, monkeypatch):
    for _ in range(5):
        lim.fail("1.2.3.4", "13800138000")
    assert lim.blocked("1.2.3.4", "13800138000")
    later = time.time() + 601
    monkeypatch.setattr(limiter_module.time, "time", lambda: later)   # jump 10 minutes ahead
    assert not lim.blocked("1.2.3.4", "13800138000")

def test_signup_limit_per_ip(lim):
    for _ in range(5):
        assert not lim.signup_blocked("1.2.3.4")
        lim.signup_done("1.2.3.4")
    assert lim.signup_blocked("1.2.3.4")
    assert not lim.signup_blocked("5.6.7.8")          # other networks are not affected


def test_login_cleanup_keeps_todays_signups(lim):
    for _ in range(5):
        lim.signup_done("1.2.3.4")
    lim.fail("9.9.9.9")                               # the cleanup runs here
    assert lim.signup_blocked("1.2.3.4")
