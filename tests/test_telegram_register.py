"""Owner button "Connect Telegram bot": the server calls setWebhook itself and returns only
safe info. Telegram is mocked; the token and the secret must never come back."""
import json

import alerts
import fileapp
import pytest
from conftest import login, login_driver

TOKEN, SECRET = "123456:SECRET-BOT-TOKEN-abc", "hook-secret-xyz"


@pytest.fixture
def tg(monkeypatch):
    for k, v in {"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_WEBHOOK_SECRET": SECRET, "TELEGRAM_CHAT_ID": "4242"}.items():
        monkeypatch.setenv(k, v)
    calls = []

    def fake(method, payload, timeout=5):
        calls.append((method, payload))
        return {"setWebhook": {"ok": True, "result": True},
                "getWebhookInfo": {"ok": True, "result": {"url": "https://shop.example.com/api/telegram/webhook",
                                                          "pending_update_count": 0,
                                                          "last_error_message": f"oops {TOKEN} {SECRET}"}},
                "getMe": {"ok": True, "result": {"username": "shop_bot", "id": 1}}}[method]
    monkeypatch.setattr(alerts, "telegram_call", fake)
    return calls


def register(client, **kw):
    return client.post("/api/telegram/register", json={}, **kw)


def test_owner_registers_the_webhook(client, tg):
    login(client, "owner-pass")
    r = register(client)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] and d["bot"] == "shop_bot" and d["pending_update_count"] == 0
    assert d["webhook_url"] == "https://shop.example.com/api/telegram/webhook"
    method, payload = tg[0]
    assert method == "setWebhook" and payload == {
        "url": "https://localhost/api/telegram/webhook", "secret_token": SECRET,
        "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True}
    assert [m for m, _ in tg] == ["setWebhook", "getWebhookInfo", "getMe"]


def test_token_and_secret_never_in_the_response_or_logs(client, tg, tmp_path):
    login(client, "owner-pass")
    body = register(client).get_data(as_text=True)
    assert TOKEN not in body and SECRET not in body and "***" in body
    logs = "".join(p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file()
                   and p.suffix in (".log", ".jsonl"))
    assert TOKEN not in logs and SECRET not in logs
    events = json.dumps(fileapp.audit.recent(50), default=str)
    assert "telegram_webhook_registered" in events
    assert TOKEN not in events and SECRET not in events


def test_telegram_refusal_is_reported_safely(client, tg, monkeypatch):
    monkeypatch.setattr(alerts, "telegram_call",
                        lambda m, p, timeout=5: {"ok": False, "description": f"Unauthorized {TOKEN}"})
    login(client, "owner-pass")
    r = register(client)
    assert r.status_code == 502 and "Unauthorized" in r.get_json()["error"]
    assert TOKEN not in r.get_data(as_text=True)
    monkeypatch.setattr(alerts, "telegram_call", lambda m, p, timeout=5: None)
    assert register(client).get_json()["error"] == "Telegram could not be reached"


def test_non_owners_get_403(client, tg):
    login_driver(client)
    assert register(client).status_code == 403
    client.post("/logout")
    client.get("/demo?k=" + fileapp.demo_key())
    assert register(client).status_code == 403
    assert tg == []                                           # Telegram never called


def test_logged_out_is_refused(client, tg):
    assert register(client).status_code in (401, 302)
    assert tg == []


def test_missing_env_vars_give_a_clear_error(client, tg, monkeypatch):
    login(client, "owner-pass")
    monkeypatch.delenv("TELEGRAM_TOKEN")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    r = register(client)
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert "TELEGRAM_TOKEN" in err and "TELEGRAM_WEBHOOK_SECRET" in err and "TELEGRAM_CHAT_ID" not in err
    assert tg == []


def test_csrf_form_post_is_refused(client, tg):
    login(client, "owner-pass")
    r = client.post("/api/telegram/register", data={"x": "1"})
    assert r.status_code == 400 and tg == []


def test_button_only_on_the_owner_dashboard(client):
    login(client, "owner-pass")
    assert "Connect Telegram bot" in client.get("/").get_data(as_text=True)
    client.post("/logout")
    client.get("/demo?k=" + fileapp.demo_key())
    assert "Connect Telegram bot" not in client.get("/").get_data(as_text=True)
