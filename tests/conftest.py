"""🧪 Test setup: a fake world (passwords + temporary files) so tests never touch real data."""
import os
import sys

import pytest

os.environ["DASHBOARD_PASSWORD"] = "owner-pass"
os.environ["DRIVER_PASSWORD"] = "driver-pass"
os.environ["SKIP_AUTO_RETRAIN"] = "1"
os.environ.pop("COOKIE_SECURE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fileapp                      # noqa: E402
from store import OrderStore        # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh browser + empty temporary data for every test."""
    fileapp.store = OrderStore(str(tmp_path / "orders.json"))
    monkeypatch.setattr(fileapp, "SESSION_VERSION_PATH", str(tmp_path / ".session_version"))
    monkeypatch.setattr(fileapp, "FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setattr(fileapp.time, "sleep", lambda seconds: None)
    fileapp._failed_logins.clear()
    return fileapp.app.test_client()


def login(c, password):
    return c.post("/login", data={"password": password})
