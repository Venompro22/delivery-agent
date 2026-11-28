"""Owner AI: the dashboard AI box, preview/confirm of owner commands, parse, feedback and intent-model retraining."""
from __future__ import annotations

from agent import handle_message
from flask import request, jsonify, has_request_context
from intent_nn import INTENTS, DATA, MODEL_PATH, train, load_feedback
from models import OrderStatus
from optimizer import optimize_route
from order_parser import parse_message
from products import clean as clean_product
from settings import clean as clean_settings
import json
import numpy as np
import os
import owner_ai
import re
import time

import fileapp as core


@core.app.route("/api/parse", methods=["POST"])
@core.owner_required
def parse_order_text():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return core.bad_request("text is required")
    if len(text) > 500:
        return core.bad_request("text is too long")
    return jsonify(parse_message(text))


@core.app.route("/api/agent", methods=["POST"])
@core.owner_required
def agent_message():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return core.bad_request("text is required")
    if len(text) > 500:
        return core.bad_request("text is too long")

    # 💬 An owner question about the numbers ("今天收入多少?") is answered from the stats
    question = owner_ai.answer(text, core.stats_data, core.late_now)
    if question:
        return jsonify(question)

    # 🛠️ An owner command ("李明 已付款 微信") → preview card; nothing changes before Confirm.
    # Deliver/cancel found by phone is a pasted WeChat message: the intent card below handles it.
    if core.current_role() == "owner":
        preview = core.owner_preview(text)
        if preview and not (preview.get("error") or
                            (preview["kind"] in ("deliver", "cancel") and preview["by"] == "phone")):
            return jsonify(preview)

    orders = [{"id": o.id, "name": o.customer_name, "phone": o.phone,
               "status": o.status.value} for o in core.orders_for_request()]
    result = handle_message(text, orders)
    result["intent"], result["confidence"] = core.predict_intent(text)

    if result.get("order_id"):
        order = core.store.get(result["order_id"])
        result["name"] = order.customer_name
        if result["action"] == "reply":
            route = optimize_route(core.driver, core.orders_for_request(), core.geo)
            me = next((r for r in route if r.id == order.id), None)
            if me:
                result["reply"] = f"您好{order.customer_name}，您的订单预计 {core.fmt(me.eta_epoch)}  送达"

    # 🎨 What the AI understood (for highlighting) and how many orders the message holds
    from order_parser import parse_message as _parse, parse_many as _many
    result["entities"] = _parse(text)["entities"]
    result["many"] = len(_many(text))

    # 🧠 A regular customer? Remember who they are, where "老地方" is, and how much they usually take.
    from order_parser import find_phone, find_address
    import customers
    phone = find_phone(text)
    known = customers.profile(phone, core.orders_for_request())
    if known and (result.get("intent") == "new_order" or customers.wants_usual_place(text)):
        last = known["last_order"]
        result["regular"] = {
            "name": known["name"], "count": known["count"], "phone": phone,
            "note": core.public_note(last), "lat": last.lat, "lng": last.lng,
            "usual_place": customers.wants_usual_place(text) or not find_address(text),
            "qty": customers.quantity(text) or known["usual_qty"],
        }
    return jsonify(result)


def owner_preview(text: str) -> dict | None:
    """Parse an owner command and store it for Confirm. Returns the preview card data,
    {"kind", "error"} when the command is incomplete, or None when it is not a command."""
    open_drivers = [u for u in core.users.all() if u["status"] == "approved"]
    a = owner_ai.parse_action(text, [o for o in core.orders_for_request()
                                     if o.status != OrderStatus.CANCELLED], open_drivers)
    if not a or a.get("error"):
        return a
    order = core.store.get(a["order_id"]) if a.get("order_id") else None
    names = {}
    if order:
        names["order"] = order.customer_name + (f" ({order.phone})" if order.phone else "")
        names["old"] = f"{order.price:g}" if order.price is not None else "-"
    if a["kind"] == "assign":
        names["driver"] = (core.users.get(a["driver_id"]) or {}).get("name", "?")
    if a["kind"] == "fee":
        names["old"] = f"{core.settings_store().all()['delivery_fee']:g}"
    summary = owner_ai.describe(a, **names)
    return {"action": "preview", "kind": a["kind"], "lang": a["lang"], "by": a.get("by"),
            "summary": summary, "hint": owner_ai.CONFIRM_HINT[a["lang"]],
            "token": core.pending_actions().put({**a, "summary": summary})}


def run_owner_action(a: dict) -> tuple[bool, str]:
    """Carry out a confirmed action. Everything is checked again: the order may have
    changed between the preview and the Confirm press."""
    kind = a.get("kind")
    if kind == "add_product":
        fields, error = clean_product(a.get("name"), a.get("price"), a.get("unit", ""))
        if error:
            return False, error
        core.product_store().create(fields["name"], fields["price"], fields["unit"])
    elif kind == "fee":
        values, error = clean_settings({"delivery_fee": a.get("fee")})
        if error:
            return False, error
        core.settings_store().update(**values)
    else:
        order = core.store.get(str(a.get("order_id", "")))
        if not order:
            return False, "order not found"
        if kind == "assign":
            u = core.users.get(str(a.get("driver_id", "")))
            if not u or u["status"] != "approved":
                return False, "unknown or not approved driver"
            order.driver_id = u["id"]
        elif kind == "deliver":
            if order.status != OrderStatus.PENDING:
                return False, "order is not open"
            core._mark_delivered(order)
        elif kind == "paid":
            if a.get("method") not in core.PAY_METHODS:
                return False, "unknown payment method"
            order.pay_method, order.paid_at = a["method"], time.time()
        elif kind == "cancel":
            if order.status == OrderStatus.DELIVERED:
                return False, "order is already delivered"
            order.status = OrderStatus.CANCELLED
        elif kind == "price":
            try:
                price = round(float(a.get("price")), 2)
            except (TypeError, ValueError):
                return False, "price must be a number"
            if not 0 <= price <= 100000:
                return False, "price is out of range"
            order.price = price
        else:
            return False, "unknown action"
        core.store.save()
        if kind == "deliver":
            core._learn_place(order)
    return True, owner_ai.DONE.get(a.get("lang"), owner_ai.DONE["en"]) + a.get("summary", "")


def confirm_owner_action(token: str, who: str) -> tuple[bool, str]:
    """Confirm button (web or Telegram): run the stored action once, and log it."""
    a = core.pending_actions().take(token)
    if not a:
        return False, "this preview expired or was already used"
    ok, message = core.run_owner_action(a)
    if ok:
        core.audit.add("ai_action", core.client_ip() if has_request_context() else "", who, a.get("summary", "")[:200])
    return ok, message


@core.app.route("/api/agent/confirm", methods=["POST"])
@core.owner_required
def agent_confirm():
    if core.current_role() != "owner":
        return jsonify({"ok": False, "error": "owner only"}), 403
    ok, message = core.confirm_owner_action((request.get_json(silent=True) or {}).get("token", ""), "owner")
    return (jsonify({"ok": True, "reply": message}) if ok
            else core.bad_request(message))


@core.app.route("/api/feedback", methods=["POST"])
@core.owner_required
def save_feedback():
    """🎡 Data flywheel: every confirmed/corrected message becomes a training example."""
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    intent = str(body.get("intent", "")).strip()
    if not text or len(text) > 500:
        return core.bad_request("text must be 1-500 characters")
    if intent not in INTENTS:
        return core.bad_request("unknown intent")
    text = re.sub(r"1[3-9]\d{9}", "", text).strip()   # 🔒 never store phone numbers
    with open(core.FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"text": text, "intent": intent, "time": time.time()},
                           ensure_ascii=False) + "\n")
    with open(core.FEEDBACK_PATH, encoding="utf-8") as f:
        count = sum(1 for _ in f)
    print(f"🎡 Feedback #{count}: {intent} ← {text}", flush=True)
    return jsonify({"ok": True, "count": count})


def retrain_model():
    """🧠 Re-learn the intent model from built-in DATA + every feedback example."""
    examples = DATA + load_feedback()
    W, b, vocab = train(examples, verbose=False)
    np.savez(MODEL_PATH, W=W, b=b, vocab=vocab)
    real = len(load_feedback(include_synthetic=False))
    return len(examples), real, len(examples) - len(DATA) - real


# Train on start; even when skipping is asked (tests), a fresh clone has no model file yet
if os.environ.get("SKIP_AUTO_RETRAIN") != "1" or not os.path.exists(MODEL_PATH):
    try:   # on every start/deploy, include the feedback collected on this server
        _n, _fb, _syn = retrain_model()
        print(f"🧠 Intent model ready: {_n} examples ({_fb} real feedback + {_syn} synthetic)", flush=True)
    except Exception as e:
        print(f"⚠️ Could not retrain at start: {e}", flush=True)


@core.app.route("/api/retrain", methods=["POST"])
@core.owner_required
def retrain():
    total, from_feedback, synthetic = retrain_model()
    print(f"🧠 Retrained: {total} examples ({from_feedback} real feedback + {synthetic} synthetic)", flush=True)
    return jsonify({"ok": True, "examples": total, "from_feedback": from_feedback})
