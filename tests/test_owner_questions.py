"""Owner AI, step 1: questions about the numbers, answered in Chinese, English and Darija
with exactly the numbers /api/stats returns."""
import json
import time

import fileapp
import owner_ai
from conftest import login
from models import Order, OrderStatus


def delivered(price, driver_id=None, late=False, items=None, days_ago=0):
    now = time.time() - days_ago * 86400
    o = Order(customer_name="Kehu", lat=30.25, lng=119.72, status=OrderStatus.DELIVERED, price=price,
              driver_id=driver_id, items=json.dumps(items or []))
    o.created_at, o.delivered_at = now - 600, now
    o.promised_by = now - 60 if late else now + 60
    fileapp.store.add(o)


def ask(client, text):
    return client.post("/api/agent", json={"text": text}).get_json()


def setup_shop(client):
    login(client, "owner-pass")
    d = fileapp.users.create("13900139001", "Ahmed", "driver-pass-42")
    fileapp.users.set_status(d["id"], "approved")
    water = {"id": "p1", "name": "桶装水", "unit": "桶", "price": 18, "qty": 3}
    delivered(54, d["id"], items=[water])
    delivered(20, d["id"], late=True)
    delivered(30, days_ago=3)
    return client.get("/api/stats").get_json()


def test_revenue_today_and_week_in_three_languages(client):
    stats = setup_shop(client)
    assert stats["revenue_today"] == 74 and stats["revenue_week"] == 104
    r = ask(client, "今天收入多少?")
    assert r["action"] == "answer" and r["lang"] == "zh" and "74" in r["reply"]
    r = ask(client, "how much revenue this week?")
    assert r["lang"] == "en" and r["topic"] == "revenue_week" and "104" in r["reply"]
    r = ask(client, "ch7al dkhlna lyoum")
    assert r["lang"] == "darija" and r["topic"] == "revenue_today" and "74" in r["reply"]
    r = ask(client, "شحال دخلنا هاد السيمانة")
    assert r["lang"] == "darija" and r["topic"] == "revenue_week" and "104" in r["reply"]


def test_late_best_driver_top_products(client):
    stats = setup_shop(client)
    r = ask(client, "有几单迟到了?")
    assert r["topic"] == "late" and "1" in r["reply"] and f"{stats['on_time_pct']}%" in r["reply"]
    r = ask(client, "who is the best driver?")
    assert r["topic"] == "best_driver" and "Ahmed" in r["reply"] and "2" in r["reply"]
    r = ask(client, "chkoun a7san livreur")
    assert r["lang"] == "darija" and "Ahmed" in r["reply"]
    r = ask(client, "哪个产品卖得最好?")
    assert r["topic"] == "top_products" and "桶装水 ×3桶" in r["reply"]


def test_no_data_answers(client):
    login(client, "owner-pass")
    assert "No driver" in ask(client, "best driver?")["reply"]
    assert "没有" in ask(client, "什么产品卖得最好?")["reply"]


def test_pasted_wechat_messages_still_go_to_the_intent_model():
    for text in ["车坏了 会晚到", "路上堵车 要迟到了", "两桶水多少钱?", "司机在哪里 多久到", "我要两桶水 文三路100号"]:
        assert owner_ai.question_topic(text) is None, text


def test_detect_lang():
    assert owner_ai.detect_lang("今天") == "zh"
    assert owner_ai.detect_lang("شحال") == "darija"
    assert owner_ai.detect_lang("wach kayn chi retard") == "darija"
    assert owner_ai.detect_lang("show me revenue") == "en"
