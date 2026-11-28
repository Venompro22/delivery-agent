"""🚨 Unhandled server errors reach the owner on Telegram, throttled, with no private data."""
import pytest
from flask import Flask, got_request_exception

import error_alerts
import fileapp


class NowThread:
    """Runs the sender right away so tests can see the alert."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def small_app(monkeypatch):
    monkeypatch.setenv("ERROR_ALERTS", "1")
    monkeypatch.setattr(error_alerts.threading, "Thread", NowThread)
    error_alerts.reset()
    sent = []
    app = Flask("boom_app")
    app.testing = True

    @app.route("/api/orders/<order_id>/deliver")
    def deliver(order_id):
        {}["secret-phone-13900000001"]          # KeyError whose text holds private data

    error_alerts.install(app, sent.append)
    return app.test_client(), sent


def hit(client, url="/api/orders/abc123/deliver?phone=13900000001"):
    with pytest.raises(KeyError):
        client.get(url)


def test_error_is_reported_without_private_data(small_app):
    client, sent = small_app
    hit(client)
    assert len(sent) == 1
    text = sent[0]
    assert "GET /api/orders/<order_id>/deliver" in text
    assert "KeyError at" in text and "test_error_alerts.py" in text
    assert "13900000001" not in text and "abc123" not in text


def test_same_error_is_throttled(small_app):
    client, sent = small_app
    for _ in range(5):
        hit(client)
    assert len(sent) == 1


def test_repeats_are_counted_in_the_next_alert():
    error_alerts.reset()
    assert error_alerts.should_send("k", 0) == (True, 0)
    assert error_alerts.should_send("k", 10)[0] is False
    assert error_alerts.should_send("k", 20)[0] is False
    assert error_alerts.should_send("k", 700) == (True, 2)


def test_hourly_cap():
    error_alerts.reset()
    results = [error_alerts.should_send(f"k{i}", 100 + i)[0] for i in range(30)]
    assert results.count(True) == error_alerts.MAX_PER_HOUR


def test_silent_under_pytest_by_default(small_app, monkeypatch):
    client, sent = small_app
    monkeypatch.delenv("ERROR_ALERTS")
    hit(client)
    assert sent == []


def test_switched_off_with_env(small_app, monkeypatch):
    client, sent = small_app
    monkeypatch.setenv("ERROR_ALERTS", "0")
    hit(client)
    assert sent == []


def test_the_real_app_is_wired():
    assert any(getattr(r, "__name__", "") == "on_exception"
               for r in got_request_exception.receivers_for(fileapp.app))
