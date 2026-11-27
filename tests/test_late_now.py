"""Late right now: open orders whose predicted ETA misses the promise, in the owner AI,
on Telegram, and as a once-per-order alert (never for demo orders)."""
import time

import alerts
import fileapp
import owner_ai
from conftest import login
from models import Order, OrderStatus


def driver(name="Ahmed", phone="13900139001"):
    u = fileapp.users.create(phone, name, "driver-pass-42")
    fileapp.users.set_status(u["id"], "approved")
    fileapp.users.set_location(u["id"], 30.25, 119.72)
    return u["id"]


def order(promise_min, driver_id=None, name="李明", note="", lat=30.25, lng=119.72):
    return fileapp.store.add(Order(customer_name=name, lat=lat, lng=lng, driver_id=driver_id,
                                   note=note, promised_by=time.time() + promise_min * 60))


def ask(client, text):
    return client.post("/api/agent", json={"text": text}).get_json()


def test_questions_in_three_languages_are_late_now():
    for q in ("شكون المعطلين دابا؟", "哪些单要迟到了", "which orders are late now"):
        assert owner_ai.question_topic(q) == "late_now", q
    assert owner_ai.question_topic("迟到多少单?") == "late"          # finished deliveries, unchanged
    assert owner_ai.question_topic("要迟到了") is None               # a driver's message, not a question


def test_lists_only_late_open_orders(client):
    login(client, "owner-pass")
    uid = driver()
    past = order(-10, uid, name="王芳")                              # promise already passed
    far = order(5, uid, name="张伟", lat=30.45, lng=119.72)          # ~22 km away, 5 min promise
    order(600, uid, name="刘洋")                                     # plenty of time
    order(-30, uid, name="陈静").status = OrderStatus.DELIVERED     # finished: not "late now"

    late = fileapp.late_now(fileapp.store.all())
    assert {x["id"] for x in late} == {past.id, far.id}
    assert all(x["driver"] == "Ahmed" and x["minutes_late"] > 0 for x in late)

    reply = ask(client, "which orders are late now")["reply"]
    for text in ("王芳", "张伟", "Ahmed", past.id, far.id, "min late"):
        assert text in reply
    assert "刘洋" not in reply and "陈静" not in reply


def test_unassigned_and_none_late(client):
    login(client, "owner-pass")
    assert "No open order" in ask(client, "which orders are late now")["reply"]
    order(-5)
    assert "未分配" in ask(client, "哪些单要迟到了")["reply"]


def test_telegram_answers_late_now(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s")
    calls = []
    monkeypatch.setattr(alerts, "telegram_api", lambda m, p: calls.append(p) or True)
    o = order(-5, driver())
    client.post("/api/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "s"},
                json={"message": {"chat": {"id": 4242}, "text": "شكون المعطلين دابا؟"}})
    assert o.id in calls[-1]["text"] and "min late" in calls[-1]["text"]   # Darija in, English out


def test_alert_is_sent_only_once(client, monkeypatch):
    monkeypatch.setattr(fileapp, "_watch", {"still": {}, "sent": set()})
    o = order(-5, driver())
    now = time.time()
    first = [m for m in fileapp.gps_watch(now) if "Running late" in m]
    assert len(first) == 1 and o.id in first[0]
    assert not any("Running late" in m for m in fileapp.gps_watch(now + 60))
    assert not any("Running late" in m for m in fileapp.gps_watch(now + 600))


def test_demo_orders_never_alert(client, monkeypatch):
    monkeypatch.setattr(fileapp, "_watch", {"still": {}, "sent": set()})
    order(-30, driver(), note=fileapp.DEMO_TAG + "demo")
    order(-30, note=fileapp.DEMO_TAG + "demo")
    assert not any("Running late" in m for m in fileapp.gps_watch(time.time()))
