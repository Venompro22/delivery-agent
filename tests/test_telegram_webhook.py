"""Owner AI, step 4: Telegram webhook. Only TELEGRAM_CHAT_ID, only with the secret header."""
import alerts
import fileapp
import pytest
from models import Order, OrderStatus

CHAT, SECRET = "4242", "hook-secret"


@pytest.fixture
def tg(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    calls = []
    monkeypatch.setattr(alerts, "telegram_api", lambda method, payload: calls.append((method, payload)) or True)
    return calls


def post(client, update, secret=SECRET):
    return client.post("/api/telegram/webhook", json=update,
                       headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def say(client, text, chat=CHAT):
    return post(client, {"message": {"chat": {"id": int(chat)}, "text": text}})


def press(client, data, chat=CHAT):
    return post(client, {"callback_query": {"id": "1", "data": data, "message": {"chat": {"id": int(chat)}}}})


def sent_texts(calls):
    return [p["text"] for m, p in calls if m == "sendMessage"]


def test_question_is_answered_with_the_stats(client, tg):
    o = Order(customer_name="K", lat=30.2, lng=119.7, status=OrderStatus.DELIVERED, price=40)
    o.delivered_at = o.created_at
    fileapp.store.add(o)
    assert say(client, "ch7al dkhlna lyoum").status_code == 200
    assert "40" in sent_texts(tg)[-1]


def test_command_previews_then_confirm_button_runs_it(client, tg):
    o = fileapp.store.add(Order(customer_name="李明", lat=30.2, lng=119.7, phone="13912345678"))
    say(client, "已送达 13912345678")                       # by phone is fine from Telegram
    payload = [p for m, p in tg if m == "sendMessage"][-1]
    yes = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert fileapp.store.get(o.id).status == OrderStatus.PENDING      # preview only
    press(client, yes)
    assert fileapp.store.get(o.id).status == OrderStatus.DELIVERED
    assert "✅" in sent_texts(tg)[-1]
    press(client, yes)                                       # second press does nothing
    assert "⚠️" in sent_texts(tg)[-1]


def test_cancel_button_drops_the_action(client, tg):
    o = fileapp.store.add(Order(customer_name="李明", lat=30.2, lng=119.7, phone="13912345678"))
    say(client, "取消 李明")
    no = [p for m, p in tg if m == "sendMessage"][-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
    press(client, no)
    press(client, "ok:" + no.split(":", 1)[1])               # the token is gone
    assert fileapp.store.get(o.id).status == OrderStatus.PENDING


def test_other_chats_and_bad_secrets_are_refused(client, tg):
    o = fileapp.store.add(Order(customer_name="李明", lat=30.2, lng=119.7, phone="13912345678"))
    say(client, "今天收入多少?", chat="999")
    assert tg == []                                          # no reply to strangers
    say(client, "取消 李明")
    yes = [p for m, p in tg if m == "sendMessage"][-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    press(client, yes, chat="999")
    assert fileapp.store.get(o.id).status == OrderStatus.PENDING
    assert say_with_secret(client, "wrong").status_code == 403


def say_with_secret(client, secret):
    return post(client, {"message": {"chat": {"id": int(CHAT)}, "text": "hi"}}, secret=secret)


def test_webhook_is_off_until_configured(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    assert say(client, "hi").status_code == 404


def test_unknown_text_gets_help(client, tg):
    say(client, "hello")
    assert "revenue" in sent_texts(tg)[-1]
