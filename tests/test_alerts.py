"""🚨 Security alerts: urgent events are written, everyday activity is not."""
import pytest

import alerts


@pytest.fixture(autouse=True)
def temp_log(tmp_path, monkeypatch):
    """Each test writes to its own alert file."""
    monkeypatch.setattr(alerts, "ALERT_LOG", str(tmp_path / "alerts.log"))
    # Make sure no test ever tries to reach Telegram.
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_urgent_event_is_logged():
    alerts.notify("login_owner", "1.2.3.4", "owner")
    lines = alerts.recent()
    assert len(lines) == 1
    assert "URGENT" in lines[0] and "login_owner" in lines[0]


def test_important_event_is_logged_as_info():
    alerts.notify("signup", "1.2.3.4", "13800138000")
    assert "[INFO]" in alerts.recent()[0]


def test_everyday_event_is_ignored():
    alerts.notify("login", "1.2.3.4", "driver")
    alerts.notify("order_created", "1.2.3.4", "owner")
    assert alerts.recent() == []


def test_details_are_included():
    alerts.notify("panic_off", "9.9.9.9", "owner", "from phone")
    assert "from phone" in alerts.recent()[0]


def test_newest_first():
    alerts.notify("2fa_failed", "1.1.1.1", "owner")
    alerts.notify("panic_lockdown", "2.2.2.2", "owner")
    assert "panic_lockdown" in alerts.recent()[0]


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "ALERT_LOG", str(tmp_path / "nope" / "x.log"))
    alerts.notify("login_owner", "1.2.3.4", "owner")   # must not raise
    assert alerts.recent() == []
