"""Reply language rule: Chinese characters in the message → Chinese reply, anything else → English.
Darija/Arabic is still understood, but no reply ever contains Arabic script."""
import json
import re

import alerts
import fileapp
import owner_ai
from conftest import login
from models import Order
from test_customer_ordering import as_customer, owner_adds_products

ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
CJK = re.compile(r"[一-鿿]")
DARIJA = ["شحال دخلنا اليوم؟", "ch7al dkhlna lyoum", "شكون أحسن ليفرور؟", "شكون المعطلين دابا؟",
          "شنو كيتباع بزاف؟", "lghi Karim", "Karim khlas wechat", "taman tawsil 5", "زيد سلعة Water 2 bottle",
          "salam", "مرحبا"]


def ask(client, text):
    return client.post("/api/agent", json={"text": text}).get_json()


def test_reply_lang_rule():
    assert owner_ai.reply_lang("今天收入多少?") == "zh"
    assert owner_ai.reply_lang("lghi talab dyal 王芳") == "zh"
    for text in ("revenue today?", "شحال دخلنا", "ch7al dkhlna lyoum", ""):
        assert owner_ai.reply_lang(text) == "en", text


def test_chinese_english_darija_questions(client):
    login(client, "owner-pass")
    zh = ask(client, "今天收入多少?")
    assert zh["lang"] == "zh" and CJK.search(zh["reply"])
    en = ask(client, "how much revenue today?")
    assert en["lang"] == "en" and en["reply"].startswith("Revenue today")
    for q in ("شحال دخلنا اليوم؟", "ch7al dkhlna lyoum"):
        r = ask(client, q)
        assert r["topic"] == "revenue_today" and r["lang"] == "en", q      # still understood
        assert r["reply"].startswith("Revenue today") and not ARABIC.search(r["reply"])


def test_no_arabic_in_any_template():
    tables = [owner_ai.T, owner_ai.LATE_NOW, owner_ai.NO_DATA, owner_ai.PREVIEW, owner_ai.DONE,
              owner_ai.CONFIRM_HINT, owner_ai.HELP, owner_ai.BUTTONS, owner_ai.DROPPED, owner_ai.UNKNOWN]
    dump = json.dumps(tables, ensure_ascii=False)
    assert '"darija"' not in dump and not ARABIC.search(dump)


def test_no_arabic_in_dashboard_replies(client):
    login(client, "owner-pass")
    fileapp.store.add(Order(customer_name="Karim", lat=30.25, lng=119.72, phone="13700001111"))
    for q in DARIJA:
        r = ask(client, q)
        assert not ARABIC.search(json.dumps(r, ensure_ascii=False)), (q, r)
        if r.get("token"):                                                  # the confirm reply too
            done = client.post("/api/agent/confirm", json={"token": r["token"]}).get_json()
            assert not ARABIC.search(json.dumps(done, ensure_ascii=False)), (q, done)


def test_no_arabic_in_telegram_replies(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s")
    calls = []
    monkeypatch.setattr(alerts, "telegram_api", lambda m, p: calls.append(p) or True)
    fileapp.store.add(Order(customer_name="Karim", lat=30.25, lng=119.72, phone="13700001111"))

    def post(update):
        client.post("/api/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "s"})

    for q in DARIJA:
        post({"message": {"chat": {"id": 4242}, "text": q}})
        buttons = calls[-1].get("reply_markup")
        if buttons:                                                         # press Cancel, then a fresh Confirm
            no = buttons["inline_keyboard"][0][1]["callback_data"]
            post({"callback_query": {"id": "1", "data": no, "message": {"chat": {"id": 4242}}}})
    assert calls and not ARABIC.search(json.dumps(calls, ensure_ascii=False))
    assert "Revenue today" in json.dumps(calls)


def test_customer_ai_order_reply_language(client):
    owner_adds_products(client)
    as_customer(client)
    assert client.post("/api/me/ai-order", json={"text": "两桶水送到5号楼302"}).get_json()["lang"] == "zh"
    for text in ("2 water to bldg 5", "جوج د الما", "bghit 2 l7lib"):
        d = client.post("/api/me/ai-order", json={"text": text}).get_json()
        assert d["lang"] == "en" and not ARABIC.search(json.dumps({k: v for k, v in d.items() if k != "unmatched"},
                                                                     ensure_ascii=False))
    page = client.get("/me").get_data(as_text=True)
    assert "TEXTS[d.lang]" in page
