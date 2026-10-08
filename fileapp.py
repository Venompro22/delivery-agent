"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, render_template_string,
                   session, redirect, url_for)

from models import Order, Driver, OrderStatus
from geo import GeoService
from optimizer import optimize_route, route_summary
from store import OrderStore
from order_parser import parse_message
from agent import handle_message
import numpy as np
from intent_nn import INTENTS, DATA, MODEL_PATH, predict_intent, train, load_feedback

app = Flask(__name__)


# ---------- Security ----------
def _load_secret_key() -> str:
    """Key that signs the login cookie. Kept in a file so logins survive restarts."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    DASHBOARD_PASSWORD = secrets.token_urlsafe(8)
    print("WARNING: DASHBOARD_PASSWORD is not set.", flush=True)
    print(f"Temporary password for this run: {DASHBOARD_PASSWORD}", flush=True)

_failed_logins = {}


def is_logged_in() -> bool:
    return session.get("auth") is True


def login_required(view):
    """Pages redirect to /login; API calls get 401."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_logged_in():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "login required"}), 401
        return redirect(url_for("login"))
    return wrapper


def too_many_attempts(ip: str) -> bool:
    count, first = _failed_logins.get(ip, (0, 0.0))
    if time.time() - first > 600:
        _failed_logins.pop(ip, None)
        return False
    return count >= 5

driver = Driver(
    name="Driver 1",
    lat=float(os.environ.get("DRIVER_LAT", "31.2304")),
    lng=float(os.environ.get("DRIVER_LNG", "121.4737")),
)
geo = GeoService(avg_speed_kmh=driver.avg_speed_kmh)
store = OrderStore()


def fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "--"


def bad_request(message):
    return jsonify({"ok": False, "error": message}), 400


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
        if too_many_attempts(ip):
            error = "Too many attempts. Wait 10 minutes."
        else:
            given = request.form.get("password", "")
            if hmac.compare_digest(given.encode(), DASHBOARD_PASSWORD.encode()):
                _failed_logins.pop(ip, None)
                session.clear()
                session["auth"] = True
                session.permanent = True
                print(f"Login from {ip}", flush=True)
                return redirect(url_for("home"))
            count, first = _failed_logins.get(ip, (0, time.time()))
            _failed_logins[ip] = (count + 1, first)
            print(f"Wrong password from {ip}", flush=True)
            time.sleep(1)
            error = "Wrong password."
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    return render_template_string(PAGE)


@app.route("/api/route")
@login_required
def api_route():
    route = optimize_route(driver, store.all(), geo)
    summary = route_summary(route)
    late_ids = set(summary.get("late_orders", []))
    delivered = sum(1 for o in store.all() if o.status == OrderStatus.DELIVERED)
    data = {
        "driver": {"lat": driver.lat, "lng": driver.lng, "name": driver.name},
        "summary": {
            "stops": summary["stops"],
            "total_km": summary["total_km"],
            "finish": fmt(summary.get("finish_epoch")),
            "late_count": len(late_ids),
            "delivered": delivered,
        },
        "stops": [
            {
                "sequence": o.sequence,
                "id": o.id,
                "name": o.customer_name,
                "phone": o.phone,
                "note": o.note,
                "lat": o.lat,
                "lng": o.lng,
                "eta": fmt(o.eta_epoch),
                "leg_km": o.leg_distance_km,
                "promise": fmt(o.promised_by) if o.promised_by else None,
                "late": o.id in late_ids,
            }
            for o in route
        ],
    }
    return jsonify(data)


@app.route("/api/orders", methods=["POST"])
@login_required
def add_order():
    body = request.get_json(silent=True) or {}

    # 1) Required fields must not be empty.
    name = str(body.get("name", "")).strip()
    lat_raw = str(body.get("lat", "")).strip()
    lng_raw = str(body.get("lng", "")).strip()
    if not name or not lat_raw or not lng_raw:
        return bad_request("name, lat and lng are required")

    # 2) lat/lng must be real numbers in a valid range.
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except ValueError:
        return bad_request("lat and lng must be numbers")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return bad_request("lat/lng out of range")

    # 3) Phone is optional, but if given it must look like a phone number.
    phone = str(body.get("phone", "")).strip()
    if phone and not re.fullmatch(r"[0-9+\-\s()]{6,20}", phone):
        return bad_request("phone looks invalid")

    # 4) Note (address details) is optional, max 200 characters.
    note = str(body.get("note", "")).strip()
    if len(note) > 200:
        return bad_request("note is too long (max 200 characters)")

    # 5) Promise is optional, but if given it must be a positive number.
    kw = {}
    promise_raw = str(body.get("promise_minutes", "")).strip()
    if promise_raw:
        try:
            minutes = float(promise_raw)
        except ValueError:
            return bad_request("promise must be a number of minutes")
        if minutes <= 0:
            return bad_request("promise must be positive")
        kw["promised_by"] = time.time() + minutes * 60

    order = Order(customer_name=name, lat=lat, lng=lng, phone=phone, note=note, **kw)
    store.add(order)
    print(f"📦 New order: {order.customer_name} ({order.lat}, {order.lng})", flush=True)
    return jsonify({"ok": True, "id": order.id})


@app.route("/api/orders/<order_id>/deliver", methods=["POST"])
@login_required
def deliver_order(order_id):
    order = store.get(order_id)
    if order:
        order.status = OrderStatus.DELIVERED
        order.delivered_at = time.time()
        store.save()
        print(f"✅ Delivered: {order.customer_name}", flush=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

@app.route("/api/orders/<order_id>/cancel", methods=["POST"])
@login_required
def cancel_order(order_id):
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    if order.status == OrderStatus.DELIVERED:
        return bad_request("order is already delivered")
    order.status = OrderStatus.CANCELLED
    store.save()
    print(f"❌ Cancelled: {order.customer_name}", flush=True)
    return jsonify({"ok": True})

@app.route("/api/orders/<order_id>", methods=["PUT"])
@login_required
def edit_order(order_id):
  order = store.get(order_id)
  if not order:
    return jsonify({"ok": False}), 404
  body = request.get_json(silent=True) or {}
  name = str(body.get("name", order.customer_name)).strip()
  phone = str(body.get("phone", order.phone)).strip()
  note = str(body.get("note", order.note)).strip()
  if not name:
    return bad_request("name is required")
  if phone and not re.fullmatch(r"[0-9+\-\s()]{6,20}", phone):
    return bad_request("phone looks invalid")
  if len(note) > 200:
    return bad_request("note is too long (max 200 characters)")
  order.customer_name = name
  order.phone = phone
  order.note = note
  store.save()
  print(f"✏️ Edited: {order.customer_name}", flush=True)
  return jsonify({"ok": True})
@app.route("/api/parse", methods=["POST"])
@login_required
def parse_order_text():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return bad_request("text is required")
    if len(text) > 500:
        return bad_request("text is too long")
    return jsonify(parse_message(text))
@app.route("/api/agent", methods=["POST"])
@login_required
def agent_message():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return bad_request("text is required")
    if len(text) > 500:
        return bad_request("text is too long")

    orders = [{"id": o.id, "name": o.customer_name, "phone": o.phone,
               "status": o.status.value} for o in store.all()]
    result = handle_message(text, orders)
    result["intent"], result["confidence"] = predict_intent(text)

    if result.get("order_id"):
        order = store.get(result["order_id"])
        result["name"] = order.customer_name
        if result["action"] == "reply":
            route = optimize_route(driver, store.all(), geo)
            me = next((r for r in route if r.id == order.id), None)
            if me:
                result["reply"] = f"您好{order.customer_name}，您的订单预计 {fmt(me.eta_epoch)} 送达 🚚"
    return jsonify(result)
FEEDBACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl")


@app.route("/api/feedback", methods=["POST"])
@login_required
def save_feedback():
    """🎡 Data flywheel: every confirmed/corrected message becomes a training example."""
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    intent = str(body.get("intent", "")).strip()
    if not text or len(text) > 500:
        return bad_request("text must be 1-500 characters")
    if intent not in INTENTS:
        return bad_request("unknown intent")
    text = re.sub(r"1[3-9]\d{9}", "", text).strip()   # 🔒 never store phone numbers
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"text": text, "intent": intent, "time": time.time()},
                           ensure_ascii=False) + "\n")
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        count = sum(1 for _ in f)
    print(f"🎡 Feedback #{count}: {intent} ← {text}", flush=True)
    return jsonify({"ok": True, "count": count})


def retrain_model():
    """🧠 Re-learn the intent model from built-in DATA + every feedback example."""
    examples = DATA + load_feedback()
    W, b, vocab = train(examples, verbose=False)
    np.savez(MODEL_PATH, W=W, b=b, vocab=vocab)
    return len(examples), len(examples) - len(DATA)


try:   # on every start/deploy, include the feedback collected on this server
    _n, _fb = retrain_model()
    print(f"🧠 Intent model ready: {_n} examples ({_fb} from feedback)", flush=True)
except Exception as e:
    print(f"⚠️ Could not retrain at start: {e}", flush=True)


@app.route("/api/retrain", methods=["POST"])
@login_required
def retrain():
    total, from_feedback = retrain_model()
    print(f"🧠 Retrained: {total} examples ({from_feedback} from feedback)", flush=True)
    return jsonify({"ok": True, "examples": total, "from_feedback": from_feedback})


DEMO_TAG = "🎬 "
DEMO_ORDERS = [   # name, phone, address, promise in minutes (None = no promise), km north, km east
    ("张伟", "13800138001", "文三路100号 5号楼3楼", 40, 1.2, 0.8),
    ("李娜", "13900139002", "阳光小区 3单元 502室", 25, -0.9, 1.5),
    ("王芳", "15800158003", "图书馆 前台", None, 2.0, -1.1),
    ("刘洋", "13700137004", "西湖区 公司前台", 60, -1.6, -0.7),
    ("陈静", "18800188005", "学生宿舍 8号楼", 3, 0.5, 2.3),     # tight promise → shows LATE ⏰
    ("赵磊", "13600136006", "农贸市场 门口", None, 2.6, 1.9),
]


@app.route("/api/demo", methods=["POST"])
@login_required
def demo_orders():
    """✨ Add realistic demo orders around the driver (great for showing the app)."""
    import math
    ids = []
    for name, phone, note, promise, north_km, east_km in DEMO_ORDERS:
        lat = driver.lat + north_km / 111.0
        lng = driver.lng + east_km / (111.0 * math.cos(math.radians(driver.lat)))
        kw = {"promised_by": time.time() + promise * 60} if promise else {}
        order = Order(customer_name=name, lat=round(lat, 5), lng=round(lng, 5),
                      phone=phone, note=DEMO_TAG + note, **kw)
        store.add(order)
        ids.append(order.id)
    print(f"✨ Demo: added {len(ids)} orders", flush=True)
    return jsonify({"ok": True, "ids": ids})


@app.route("/api/demo/clear", methods=["POST"])
@login_required
def clear_demo():
    """🧹 Remove every demo order (real orders are never touched)."""
    before = len(store.orders)
    store.orders = [o for o in store.orders if not (o.note or "").startswith(DEMO_TAG)]
    store.save()
    removed = before - len(store.orders)
    print(f"🧹 Demo: removed {removed} orders", flush=True)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/driver/location", methods=["POST"])
@login_required
def update_driver_location():
    """The driver's phone sends its GPS position; routes start from here."""
    body = request.get_json(silent=True) or {}
    try:
        lat = float(body.get("lat"))
        lng = float(body.get("lng"))
    except (TypeError, ValueError):
        return bad_request("lat and lng must be numbers")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return bad_request("lat/lng out of range")
    driver.lat, driver.lng = lat, lng
    print(f"📍 Driver location: ({lat:.5f}, {lng:.5f})", flush=True)
    return jsonify({"ok": True, "lat": lat, "lng": lng})


@app.route("/orders")
@login_required
def orders_page():
    return render_template_string(ORDERS_PAGE)


@app.route("/api/orders")
@login_required
def list_orders():
    """Every order, newest first — delivered ones included."""
    today = datetime.now().date()
    items = []
    for o in sorted(store.all(), key=lambda x: x.created_at or 0, reverse=True):
        created = datetime.fromtimestamp(o.created_at) if o.created_at else None
        items.append({
            "id": o.id,
            "name": o.customer_name,
            "phone": o.phone,
            "note": o.note,
            "lat": o.lat,
            "lng": o.lng,
            "status": o.status.value,
            "created": created.strftime("%m-%d %H:%M") if created else "--",
            "today": bool(created and created.date() == today),
            "promise": fmt(o.promised_by) if o.promised_by else None,
            "delivered": fmt(o.delivered_at) if o.delivered_at else None,
            "minutes": round((o.delivered_at - o.created_at) / 60)
                       if o.delivered_at and o.created_at else None,
        })
    return jsonify(items)


@app.route("/api/track/<order_id>")
def api_track(order_id):
    """Public info for ONE order — safe to share with the customer."""
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "error": "order not found"}), 404
    if order.status == OrderStatus.DELIVERED:
        return jsonify({"ok": True, "status": "delivered", "name": order.customer_name})
    if order.status == OrderStatus.CANCELLED:
        return jsonify({"ok": True, "status": "cancelled", "name": order.customer_name})

    # Where is this order in the current route?
    route = optimize_route(driver, store.all(), geo)
    me = next((o for o in route if o.id == order_id), None)
    return jsonify({
        "ok": True,
        "status": "on_the_way",
        "name": order.customer_name,
        "position": me.sequence if me else None,
        "stops_before": (me.sequence - 1) if me else None,
        "eta": fmt(me.eta_epoch) if me else "--",
        "promise": fmt(order.promised_by) if order.promised_by else None,
        "lat": order.lat,
        "lng": order.lng,
    })


@app.route("/track/<order_id>")
def track_page(order_id):
    return render_template_string(TRACK_PAGE, order_id=order_id)


PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Delivery Dashboard</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, "Segoe UI", sans-serif;
      margin: 0; display: flex; height: 100vh; background: #0f1115;
    }
    #sidebar {
      width: 360px; padding: 20px; overflow-y: auto;
      background: #1a1d24; color: #e8eaed;
      box-shadow: 2px 0 12px rgba(0,0,0,0.3);
    }
    .brand {
      display: flex; align-items: center; gap: 10px;
      padding-bottom: 16px; border-bottom: 1px solid #2a2e37; margin-bottom: 16px;
    }
    .brand .logo {
      width: 42px; height: 42px; border-radius: 11px;
      background: linear-gradient(135deg, #2d6cdf, #1f9d55);
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; flex-shrink: 0;
    }
    .brand h2 { margin: 0; font-size: 18px; }
    .logout {
      margin-left: auto; font-size: 11px; color: #8a8f98; text-decoration: none;
      border: 1px solid #2a2e37; border-radius: 6px; padding: 5px 8px;
    }
    .logout:hover { color: #e8eaed; border-color: #8a8f98; }
    .subtitle { color: #8a8f98; font-size: 12px; }
    #sidebar h3 {
      font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
      color: #8a8f98; margin: 24px 0 12px;
    }
    .hint {
      font-size: 12px; color: #8a8f98; background: #0f1115;
      border: 1px dashed #2a2e37; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px;
    }
    .coords { display: flex; gap: 8px; }
    .coords input { flex: 1; }
    .error {
      display: none; color: #ff8a8d; background: rgba(229,72,77,0.12);
      border: 1px solid #e5484d; border-radius: 8px; padding: 8px 10px;
      font-size: 13px; margin-top: 8px;
    }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 16px 0; }
    .stat {
      background: #0f1115; border: 1px solid #2a2e37;
      border-radius: 10px; padding: 10px 4px; text-align: center;
    }
    .stat .num { font-size: 18px; font-weight: 700; color: #2d6cdf; }
    .stat.done .num { color: #1f9d55; }
    .stat .label {
      font-size: 9px; color: #8a8f98; text-transform: uppercase;
      letter-spacing: 0.5px; margin-top: 2px;
    }
    #map { flex: 1; cursor: crosshair; }
    /* 🍎 map themes */
    .tiles-dark { filter: brightness(0.62) contrast(1.15) saturate(0.6); }
    .tiles-soft { filter: saturate(0.6) brightness(1.05) contrast(0.93); }
    #map { background: #0f1115; }
    .leaflet-bar a, .leaflet-control-layers {
      background: #1a1d24 !important; color: #e8eaed !important; border-color: #2a2e37 !important;
    }
    .leaflet-control-layers { border-radius: 10px !important; box-shadow: 0 4px 14px rgba(0,0,0,0.4) !important; }
    .leaflet-control-layers-expanded { padding: 8px 12px; font-size: 13px; }
    .leaflet-popup-content-wrapper, .leaflet-popup-tip {
      background: #1a1d24; color: #e8eaed; border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,0.5);
    }
    .leaflet-control-attribution { background: rgba(15,17,21,0.7) !important; color: #8a8f98 !important; }
    .leaflet-control-attribution a { color: #8a8f98 !important; }
    .leaflet-control-scale-line { background: rgba(26,29,36,0.8); color: #e8eaed; border-color: #8a8f98; }
    .pin {
      width: 28px; height: 28px; border-radius: 50%; color: #fff; font-weight: 800; font-size: 13px;
      display: flex; align-items: center; justify-content: center;
      border: 2px solid #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.45);
    }
    .pin-driver {
      width: 36px; height: 36px; border-radius: 50%; background: #1f9d55; font-size: 19px;
      display: flex; align-items: center; justify-content: center; border: 3px solid #fff;
      box-shadow: 0 0 0 0 rgba(31,157,85,0.7); animation: driverPulse 2s infinite;
    }
    @keyframes driverPulse {
      0% { box-shadow: 0 0 0 0 rgba(31,157,85,0.7); }
      70% { box-shadow: 0 0 0 14px rgba(31,157,85,0); }
      100% { box-shadow: 0 0 0 0 rgba(31,157,85,0); }
    }
    input {
      width: 100%; padding: 11px; margin: 6px 0;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 8px;
      color: #e8eaed; font-size: 14px;
    }
    input:focus { outline: none; border-color: #2d6cdf; }
    input::placeholder { color: #5a5f68; }
    input.invalid { border-color: #e5484d; }
    .fb {
      display: none; align-items: center; gap: 6px; flex-wrap: wrap;
      font-size: 12px; color: #c3c7cd; background: #0f1115;
      border: 1px dashed #6b4fd8; border-radius: 8px; padding: 8px; margin-top: 6px;
    }
    .fb button { width: auto; margin: 0; padding: 5px 9px; font-size: 12px; }
    .fb .fb-ok { background: #1f9d55; }
    .fb .fb-fix { background: #f0a020; color: #0f1115; }
    .ai-card {
      background: #0f1115; border: 1px solid #6b4fd8; border-radius: 12px;
      padding: 12px; margin-top: 8px; font-size: 13px; color: #e8eaed;
      animation: slideIn 0.25s ease;
    }
    .ai-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .ai-who { font-size: 11px; padding: 3px 8px; border-radius: 20px; background: #1a1d24; color: #c3c7cd; }
    .ai-intent { font-weight: 700; font-size: 14px; }
    .ai-conf { margin-left: auto; font-size: 11px; color: #8a8f98; }
    .ai-bar { height: 4px; background: #1a1d24; border-radius: 4px; margin: 8px 0; overflow: hidden; }
    .ai-bar div { height: 100%; border-radius: 4px; }
    .ai-msg { color: #c3c7cd; background: #1a1d24; border-left: 3px solid #6b4fd8;
              padding: 6px 8px; border-radius: 4px; margin: 6px 0; }
    .ai-what { margin: 8px 0; }
    .ai-reply { width: 100%; padding: 8px; margin: 6px 0; font-size: 13px; resize: none;
                background: #1a1d24; color: #e8eaed; border: 1px dashed #1f9d55; border-radius: 6px; }
    .ai-actions, .ai-learn, .ai-fix { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .ai-actions button, .ai-learn button, .ai-fix button, .ai-actions a {
      width: auto; margin: 0; padding: 7px 11px; font-size: 12px; border-radius: 6px; text-decoration: none;
    }
    .ai-actions a { background: #f0a020; color: #0f1115; font-weight: 600; }
    .ai-learn { margin-top: 10px; padding-top: 8px; border-top: 1px solid #2a2e37; color: #8a8f98; font-size: 12px; }
    .ai-learn .ok { background: #1f9d55; }
    .ai-learn .fix { background: #f0a020; color: #0f1115; }
    .ai-fix { margin-top: 6px; }
    .ai-fix button { background: #1a1d24; border: 1px solid #2a2e37; color: #e8eaed; }
    .ai-fix button:hover { border-color: #6b4fd8; }
    .ai-human { color: #f0a020; }
    #ai-history { margin-top: 10px; }
    .hist-head { display: flex; align-items: center; gap: 8px; font-size: 11px; color: #8a8f98;
                 text-transform: uppercase; letter-spacing: 1px; margin: 4px 0; }
    .hist-head button { width: auto; margin: 0 0 0 auto; padding: 5px 10px; font-size: 11px;
                        background: #6b4fd8; text-transform: none; letter-spacing: 0; }
    .hist-row { display: flex; gap: 6px; font-size: 12px; color: #c3c7cd; padding: 4px 0;
                border-bottom: 1px solid #1a1d24; white-space: nowrap; overflow: hidden; }
    .hist-row .t { color: #5a5f68; }
    .hist-row .m { overflow: hidden; text-overflow: ellipsis; }
    #paste {
      width: 100%; padding: 11px; margin: 6px 0; resize: vertical;
      background: #0f1115; border: 1px dashed #6b4fd8; border-radius: 8px;
      color: #e8eaed; font-size: 14px; font-family: inherit;
    }
    button {
      width: 100%; padding: 12px; background: #2d6cdf; color: white;
      border: none; border-radius: 8px; cursor: pointer; margin-top: 10px;
      font-size: 14px; font-weight: 600; transition: background 0.15s;
    }
    button:hover { background: #245bc0; }
    .stop {
      padding: 12px; margin-bottom: 8px; font-size: 14px;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 10px;
    }
    .stop.new { animation: slideIn 0.25s ease; }
    @keyframes slideIn {
      from { opacity: 0; transform: translateY(-6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .stop.next {
      border-color: #2d6cdf;
      background: linear-gradient(180deg, rgba(45,108,223,0.12), #0f1115);
    }
    .stop.next b::before { content: "▶ "; color: #2d6cdf; }
    .stop.late {
      border-color: #e5484d;
      background: linear-gradient(180deg, rgba(229,72,77,0.12), #0f1115);
    }
    .badge {
      display: inline-block; background: #e5484d; color: #fff;
      font-size: 10px; padding: 2px 7px; border-radius: 20px;
      margin-left: 6px; font-weight: 600; vertical-align: middle;
    }
    .stop .meta { color: #8a8f98; font-size: 12px; margin-top: 3px; }
    .stop .promise { color: #f0a020; }
    .stop .note {
      color: #c3c7cd; font-size: 12px; margin-top: 6px; padding: 6px 8px;
      background: #1a1d24; border-left: 3px solid #8a8f98; border-radius: 4px;
    }
    .stop .share { background: #6b4fd8; }
    .stop .share:hover { background: #5a3fc4; }
    .stop button {
      width: auto; padding: 6px 12px; font-size: 12px;
      background: #1f9d55; margin-top: 8px; border-radius: 6px;
    }
    .stop button:hover { background: #178045; }
    .stop .actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
    .stop .actions button, .stop .actions a { margin-top: 0; }
    .stop .nav {
      display: inline-block; padding: 6px 12px; font-size: 12px; font-weight: 600;
      background: #2d6cdf; color: #fff; border-radius: 6px; text-decoration: none;
    }
    .stop .nav:hover { background: #245bc0; }
    .stop .call { background: #f0a020; color: #0f1115; }
    .stop .call:hover { background: #d88e14; }
    .live {
      display: inline-flex; align-items: center; gap: 5px;
      font-size: 11px; color: #8a8f98; margin-left: 8px; font-weight: 400;
      text-transform: none; letter-spacing: 0;
    }
    .live .dot {
      width: 7px; height: 7px; border-radius: 50%; background: #1f9d55;
      animation: pulse 1.6s infinite;
    }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    .empty { color: #5a5f68; text-align: center; padding: 20px; font-size: 13px; }
    .toast {
      position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%) translateY(20px);
      background: #1f9d55; color: #fff; padding: 12px 18px; border-radius: 10px;
      font-size: 14px; font-weight: 600; box-shadow: 0 6px 20px rgba(0,0,0,0.4);
      opacity: 0; pointer-events: none; transition: all 0.25s ease; z-index: 9999;
      max-width: 90vw; text-align: center;
    }
    .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    .stop.flash { animation: flash 1.6s ease; }
    @keyframes flash {
      0%, 100% { box-shadow: 0 0 0 0 rgba(31,157,85,0); }
      30% { box-shadow: 0 0 0 4px rgba(31,157,85,0.9); }
    }
    .gps-row { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
    .gps-btn {
      width: auto; flex: 1; margin-top: 0; background: #0f1115;
      border: 1px solid #1f9d55; color: #1f9d55;
    }
    .gps-btn:hover { background: rgba(31,157,85,0.12); }
    .gps-btn.on { background: #1f9d55; color: #fff; }
    .demo-btn { border-color: #6b4fd8; color: #b9a6ff; }
    .demo-btn:hover { background: rgba(107,79,216,0.15); }
    .demo-clear { border-color: #2a2e37; color: #8a8f98; }
    .demo-clear:hover { background: rgba(138,143,152,0.12); }
    .gps-status { font-size: 11px; color: #8a8f98; min-height: 14px; margin: -6px 0 10px; }

    @media (max-width: 768px) {
      body { flex-direction: column-reverse; height: auto; min-height: 100vh; }
      #map { flex: none; height: 50vh; width: 100%; }
      #sidebar { width: 100%; box-shadow: none; }
      input, button { font-size: 16px; }
    }
  </style>
</head>
<body>
  <div id="sidebar">
    <div class="brand">
      <div class="logo">🚚</div>
      <div>
        <h2>Delivery Agent</h2>
        <div class="subtitle">Smart routing dashboard</div>
      </div>
      <a class="logout" href="/orders" title="All orders">📋 Orders</a>
      <a class="logout" href="/logout" title="Log out">Logout</a>
    </div>

    <div class="gps-row">
      <button class="gps-btn" onclick="locateOnce()">📍 Use my location</button>
      <button class="gps-btn" id="live-btn" onclick="toggleLive()">🛰️ Live GPS: off</button>
    </div>
    <div class="gps-status" id="gps-status"></div>
    <div class="gps-row">
      <button class="gps-btn demo-btn" onclick="loadDemo()">✨ Demo</button>
      <button class="gps-btn demo-clear" onclick="clearDemo()">🧹 Clear demo</button>
    </div>
        <textarea id="paste" rows="2" placeholder="📋 Paste a WeChat message here…"></textarea>
       <button onclick="handleMessage()">🤖 Handle message</button>
    <div id="fb"></div>
    <div id="ai-history"></div>
    <input id="name" placeholder="Customer name"/>
    <input id="phone" placeholder="Phone (optional)" inputmode="tel"/>
    <input id="note" placeholder="Address / notes (optional) — e.g. Bldg 5, floor 3" maxlength="200"/>
    <div class="hint">📍 Tap the map to set the location</div>
    <div class="coords">
      <input id="lat" placeholder="Latitude" inputmode="decimal"/>
      <input id="lng" placeholder="Longitude" inputmode="decimal"/>
    </div>
    <input id="promise" placeholder="Promise in minutes (optional)" inputmode="numeric"/>
    <button onclick="addOrder()">Add order</button>
    <div id="error" class="error"></div>
    <div id="toast" class="toast"></div>

    <div class="stats">
      <div class="stat"><div class="num" id="stat-stops">0</div><div class="label">Stops</div></div>
      <div class="stat"><div class="num" id="stat-km">0</div><div class="label">Km</div></div>
      <div class="stat"><div class="num" id="stat-finish">--</div><div class="label">Finish</div></div>
      <div class="stat done"><div class="num" id="stat-done">0</div><div class="label">Done</div></div>
    </div>

    <h3>Route <span class="live"><span class="dot"></span><span id="updated">live</span></span></h3>
    <div id="stops"></div>
  </div>
  <div id="map"></div>

  <script>
    // 🗺️ map labels follow the page language (中文 / EN)
    let MAP_LANG = 'en';
    try { MAP_LANG = (localStorage.getItem('lang') || ((navigator.language || '').startsWith('zh') ? 'zh' : 'en')) === 'zh' ? 'zh_cn' : 'en'; } catch (e) {}
    const map = L.map('map').setView([31.2304, 121.4737], 13);
    // 🍎 Map styles: 🌙 Dark (Apple-like) · ☀️ Light · 🛰️ Satellite — your choice is remembered
    const TILE_SCALE = 1;
    const gaodeUrl = 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=' + MAP_LANG +
      '&size=1&scale=' + TILE_SCALE + '&style=8&x={x}&y={y}&z={z}';
    const tileOpts = {subdomains: ['1', '2', '3', '4'], maxZoom: 18, attribution: '&copy; AutoNavi'};
    const mapStyles = {
      '🌗 Dim': L.tileLayer(gaodeUrl, Object.assign({className: 'tiles-dark'}, tileOpts)),
      '☀️ Light': L.tileLayer(gaodeUrl, Object.assign({className: 'tiles-soft'}, tileOpts)),
      '🛰️ Satellite': L.layerGroup([
        L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}', tileOpts),
        L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
          {subdomains: ['1', '2', '3', '4'], maxZoom: 18}),
      ]),
    };
    let mapStyle = '☀️ Light';
    try { const saved = localStorage.getItem('mapStyle'); if (mapStyles[saved]) mapStyle = saved; } catch (e) {}
    mapStyles[mapStyle].addTo(map);
    L.control.layers(mapStyles, null, {position: 'topleft'}).addTo(map);
    map.on('baselayerchange', e => { try { localStorage.setItem('mapStyle', e.name); } catch (err) {} });
    L.control.scale({imperial: false}).addTo(map);
    let layer = L.layerGroup().addTo(map);
    let pickMarker = null;

    async function api(url, options) {
      const res = await fetch(url, options);
      if (res.status === 401) { location.href = '/login'; throw new Error('login required'); }
      return res;
    }
    let lastStopCount = -1;
    let lastSignature = '';
    const seenIds = new Set();

    map.on('click', e => {
      const lat = e.latlng.lat.toFixed(5);
      const lng = e.latlng.lng.toFixed(5);
      document.getElementById('lat').value = lat;
      document.getElementById('lng').value = lng;
      if (pickMarker) map.removeLayer(pickMarker);
      pickMarker = L.marker([lat, lng]).addTo(map)
        .bindPopup('New order here').openPopup();
      clearError();
      document.getElementById('name').focus();
    });

    function showError(msg) {
      const box = document.getElementById('error');
      box.textContent = msg;
      box.style.display = 'block';
    }
    function clearError() {
      document.getElementById('error').style.display = 'none';
      ['name','phone','note','lat','lng','promise'].forEach(id =>
        document.getElementById(id).classList.remove('invalid'));
    }

    async function addOrder() {
      clearError();
      const body = {
        name: document.getElementById('name').value.trim(),
        phone: document.getElementById('phone').value.trim(),
        note: document.getElementById('note').value.trim(),
        lat: document.getElementById('lat').value.trim(),
        lng: document.getElementById('lng').value.trim(),
        promise_minutes: document.getElementById('promise').value.trim(),
      };

      const missing = ['name','lat','lng'].filter(k => !body[k]);
      if (missing.length) {
        missing.forEach(id => document.getElementById(id).classList.add('invalid'));
        showError(missing.includes('lat') || missing.includes('lng')
          ? 'Tap the map to set the location, and enter a name.'
          : 'Please enter the customer name.');
        return;
      }

      const res = await api('/api/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { showError(data.error || 'Could not add order.'); return; }

      ['name','phone','note','lat','lng','promise'].forEach(id => document.getElementById(id).value = '');
      if (pickMarker) { map.removeLayer(pickMarker); pickMarker = null; }
      knownIds.add(data.id);
      await refresh();
      highlightNew(data.id);
    }
        async function parseMessage() {
      const text = document.getElementById('paste').value.trim();
      if (!text) { showError('Paste a message first.'); return; }
      const res = await api('/api/parse', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      const d = await res.json();
      if (!res.ok) { showError(d.error || 'Could not read the message.'); return; }

      if (d.name) document.getElementById('name').value = d.name;
      if (d.phone) document.getElementById('phone').value = d.phone;
      if (d.address) document.getElementById('note').value = d.address;
      if (d.deadline) {
        const [h, m] = d.deadline.split(':').map(Number);
        const target = new Date();
        target.setHours(h, m, 0, 0);
        const mins = Math.round((target - new Date()) / 60000);
        if (mins > 0) document.getElementById('promise').value = mins;
      }
      clearError();
      showToast('✨ Filled! Now tap the map to set the location 📍');
    }
    // ---------- 🤖 AI Inbox ----------
    const INTENT_NAMES = {new_order: '📦 New order', cancel: '❌ Cancel', question: '🤔 Question',
                          delivered: '✅ Delivered', delay: '⏰ Delay', not_home: '🏠 Not home'};
    const aiHistory = [];
    let current = null;     // the message on the card right now

    function confColor(c) { return c >= 0.8 ? '#1f9d55' : c >= 0.6 ? '#f0a020' : '#e5484d'; }

    async function copyText(t) {
      try { await navigator.clipboard.writeText(t); showToast('📋 Copied — paste it in WeChat'); }
      catch (e) { prompt('Copy this:', t); }
    }

    function actionHtml(r) {
      const name = esc(r.name || '');
      const reply = r.reply ? `<textarea class="ai-reply" rows="2" readonly>${esc(r.reply)}</textarea>` : '';
      const copy = r.reply ? `<button onclick="copyText(current.r.reply)">📋 Copy reply</button>` : '';
      switch (r.action) {
        case 'fill_form':
          return `<div class="ai-what">📦 Order form filled — <b>tap the map</b> to set the location 📍</div>`;
        case 'cancel':
          return `<div class="ai-what">❌ Wants to cancel <b>${name}</b>'s order</div>
            <div class="ai-actions"><button style="background:#e5484d" onclick="doAction('cancel')">✕ Cancel the order</button></div>`;
        case 'reply':
          return `<div class="ai-what">🤔 <b>${name}</b> is asking about the order</div>${reply}
            <div class="ai-actions">${copy}</div>`;
        case 'deliver':
          return `<div class="ai-what">✅ Driver delivered <b>${name}</b>'s order</div>
            <div class="ai-actions"><button style="background:#1f9d55" onclick="doAction('deliver')">✓ Mark as delivered</button></div>`;
        case 'notify_delay':
          return `<div class="ai-what">⏰ The driver is running late</div>${reply}
            <div class="ai-actions">${copy}</div>`;
        case 'call_customer':
          return `<div class="ai-what">🏠 <b>${name}</b> is not home</div>
            <div class="ai-actions"><a href="tel:${esc(r.phone)}">📞 Call ${esc(r.phone)}</a></div>`;
        default:
          return `<div class="ai-what ai-human">🙋 Not sure (${esc(r.reason || '')}) — please check it yourself.</div>`;
      }
    }

    function renderCard() {
      const {text, r} = current;
      const pct = Math.round((r.confidence || 0) * 100);
      const who = r.sender === 'driver' ? '🚚 Driver' : '👤 Customer';
      document.getElementById('fb').innerHTML = `<div class="ai-card">
        <div class="ai-head">
          <span class="ai-who">${who}</span>
          <span class="ai-intent">${INTENT_NAMES[r.intent] || r.intent}</span>
          <span class="ai-conf">${pct}% sure</span>
        </div>
        <div class="ai-bar"><div style="width:${pct}%;background:${confColor(r.confidence || 0)}"></div></div>
        <div class="ai-msg">💬 ${esc(text)}</div>
        ${actionHtml(r)}
        <div class="ai-learn" id="ai-learn">Was I right?
          <button class="ok" onclick="teach(current.r.intent)">✅ Right</button>
          <button class="fix" onclick="showFix()">✏️ Fix</button>
        </div>
      </div>`;
    }

    function showFix() {
      document.getElementById('ai-learn').innerHTML = 'It means: <div class="ai-fix">' +
        Object.keys(INTENT_NAMES).map(k =>
          `<button onclick="teach('${k}')">${INTENT_NAMES[k]}</button>`).join('') + '</div>';
    }

    async function teach(intent) {
      if (!current) return;
      const res = await api('/api/feedback', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: current.text, intent}),
      });
      const d = await res.json();
      if (!res.ok) { showError(d.error || 'Could not save'); return; }
      current.status = intent === current.r.intent ? '✅' : '✏️';
      document.getElementById('ai-learn').innerHTML =
        `📝 Learned! ${d.count} examples collected 🎡 — press <b>🧠 Retrain AI</b> below to use them`;
      renderHistory();
    }

    async function doAction(kind) {
      const {r} = current;
      const res = await api(`/api/orders/${r.order_id}/${kind}`, {method: 'POST'});
      if (!res.ok) { showError((await res.json()).error || 'Action failed'); return; }
      await refresh();
      showToast(kind === 'cancel' ? `✕ ${r.name}'s order cancelled` : `✅ ${r.name}'s order delivered`);
      current.status = kind === 'cancel' ? '✕' : '✓';
      renderHistory();
      if (kind === 'cancel' && r.reply) {
        document.querySelector('#fb .ai-actions').innerHTML =
          `<textarea class="ai-reply" rows="2" readonly>${esc(r.reply)}</textarea>
           <button onclick="copyText(current.r.reply)">📋 Copy reply</button>`;
      } else {
        document.querySelector('#fb .ai-actions').innerHTML = '<span>✔ Done</span>';
      }
    }

    function renderHistory() {
      const rows = aiHistory.slice(0, 8).map(h => {
        const who = h.r.sender === 'driver' ? '🚚' : '👤';
        return `<div class="hist-row"><span class="t">${h.time}</span><span>${who}</span>
          <span>${INTENT_NAMES[h.r.intent] || h.r.intent}</span><span>${h.status || ''}</span>
          <span class="m">${esc(h.text)}</span></div>`;
      }).join('');
      document.getElementById('ai-history').innerHTML = `<div class="hist-head">🤖 AI inbox
        <button onclick="retrainAI()">🧠 Retrain AI</button></div>${rows ||
        '<div class="hist-row"><span class="t">Paste a WeChat message above 👆</span></div>'}`;
    }

    async function retrainAI() {
      showToast('🧠 Training…');
      const res = await api('/api/retrain', {method: 'POST'});
      const d = await res.json();
      showToast(res.ok ? `🧠 AI retrained on ${d.examples} examples (${d.from_feedback} taught by you) 🎉`
                       : (d.error || 'Retrain failed'));
    }

    async function handleMessage() {
      const text = document.getElementById('paste').value.trim();
      if (!text) { showError('Paste a message first.'); return; }
      const res = await api('/api/agent', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      const r = await res.json();
      if (!res.ok) { showError(r.error || 'The agent could not read the message.'); return; }
      clearError();
      current = {text, r, status: '', time: new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})};
      aiHistory.unshift(current);
      renderCard();
      renderHistory();
      if (r.action === 'fill_form') await parseMessage();
      document.getElementById('paste').value = '';
    }
    renderHistory();

    async function loadDemo() {
      const res = await api('/api/demo', {method: 'POST'});
      const d = await res.json();
      if (!res.ok) { showError(d.error || 'Could not add demo orders'); return; }
      if (typeof knownIds !== 'undefined') d.ids.forEach(id => knownIds.add(id));   // no "new order" beeps
      lastStopCount = -1;          // zoom the map to fit the new route
      await refresh();
      showToast(`✨ ${d.ids.length} demo orders added — watch the route 🗺️`);
    }

    async function clearDemo() {
      const res = await api('/api/demo/clear', {method: 'POST'});
      const d = await res.json();
      lastStopCount = -1;
      await refresh();
      showToast(`🧹 ${d.removed} demo orders removed`);
    }

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      clearTimeout(t._timer);
      t._timer = setTimeout(() => t.classList.remove('show'), 3500);
    }

    // Tell the user where the new order went, and bring it into view
    function highlightNew(id) {
      const card = document.querySelector(`.stop[data-id="${id}"]`);
      if (!card) { showToast('✅ Order added'); return; }
      const pos = [...document.querySelectorAll('.stop')].indexOf(card) + 1;
      showToast(`✅ Order added — stop #${pos} in the route`);
      card.scrollIntoView({behavior: 'smooth', block: 'center'});
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1700);
    }

    // ---------- 📍 Driver GPS ----------
    let watchId = null;
    let lastSent = 0;

    function gpsStatus(msg) { document.getElementById('gps-status').textContent = msg; }

    function gpsError(err) {
      if (!window.isSecureContext) {
        gpsStatus('⚠️ GPS needs HTTPS — open the dashboard with the ngrok link.');
      } else if (err && err.code === 1) {
        gpsStatus('⚠️ Location permission denied — allow it in your browser settings.');
      } else {
        gpsStatus('⚠️ Could not get your location. Try again outside / with GPS on.');
      }
    }

    // 📍 Phones give GPS in WGS-84; Chinese maps (Gaode) use GCJ-02 → convert, or the dot is ~500 m off
    function wgs2gcj(lat, lng) {
      if (lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271) return [lat, lng];  // outside China
      const a = 6378245.0, ee = 0.00669342162296594323, PI = Math.PI;
      const x = lng - 105.0, y = lat - 35.0;
      let dLat = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
      dLat += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      dLat += (20 * Math.sin(y * PI) + 40 * Math.sin(y / 3 * PI)) * 2 / 3;
      dLat += (160 * Math.sin(y / 12 * PI) + 320 * Math.sin(y * PI / 30)) * 2 / 3;
      let dLng = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
      dLng += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      dLng += (20 * Math.sin(x * PI) + 40 * Math.sin(x / 3 * PI)) * 2 / 3;
      dLng += (150 * Math.sin(x / 12 * PI) + 300 * Math.sin(x / 30 * PI)) * 2 / 3;
      const rad = lat / 180 * PI;
      let m = Math.sin(rad); m = 1 - ee * m * m;
      const sm = Math.sqrt(m);
      dLat = (dLat * 180) / ((a * (1 - ee)) / (m * sm) * PI);
      dLng = (dLng * 180) / (a / sm * Math.cos(rad) * PI);
      return [lat + dLat, lng + dLng];
    }

    async function sendLocation(pos) {
      const [lat, lng] = wgs2gcj(pos.coords.latitude, pos.coords.longitude);
      const acc = Math.round(pos.coords.accuracy);
      const res = await api('/api/driver/location', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lat, lng}),
      });
      if (!res.ok) { gpsStatus('⚠️ Server rejected the location.'); return; }
      lastSent = Date.now();
      gpsStatus(`📍 You are here (±${acc} m) · ${new Date().toLocaleTimeString()}`);
      lastStopCount = -1;
      refresh();
    }

    function locateOnce() {
      if (!navigator.geolocation || !window.isSecureContext) { gpsError(); return; }
      gpsStatus('Locating…');
      navigator.geolocation.getCurrentPosition(sendLocation, gpsError,
        {enableHighAccuracy: true, timeout: 15000, maximumAge: 0});
    }

    function toggleLive() {
      const btn = document.getElementById('live-btn');
      if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
        btn.classList.remove('on');
        btn.textContent = '🛰️ Live GPS: off';
        gpsStatus('Live GPS stopped.');
        return;
      }
      if (!navigator.geolocation || !window.isSecureContext) { gpsError(); return; }
      btn.classList.add('on');
      btn.textContent = '🛰️ Live GPS: on';
      gpsStatus('Starting live GPS…');
      watchId = navigator.geolocation.watchPosition(pos => {
        if (Date.now() - lastSent > 20000) sendLocation(pos);
      }, gpsError, {enableHighAccuracy: true, maximumAge: 10000});
    }

    function esc(t) {
      return String(t).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }

    async function share(id) {
      const url = `${location.origin}/track/${id}`;
      try {
        await navigator.clipboard.writeText(url);
        alert('Tracking link copied ✅ ' + url);
      } catch (e) {
        prompt('Copy this tracking link for the customer:', url);
      }
    }

    function navUrl(s) {
      const name = encodeURIComponent(s.name);
      return `https://uri.amap.com/navigation?to=${s.lng},${s.lat},${name}&mode=car&callnative=1`;
    }

    async function deliver(id) {
      await api(`/api/orders/${id}/deliver`, {method: 'POST'});
      refresh();
    }

    // ---------- 🔔 Alerts: new orders + late orders ----------
    let alertsReady = false;
    const knownIds = new Set();
    const lateSeen = new Set();
    let audioCtx = null;

    // Browsers only allow sound/notifications after the user clicks once
    document.addEventListener('click', () => {
      try { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
      if (window.Notification && Notification.permission === 'default') Notification.requestPermission();
    }, {once: true});

    function beep(freq, ms) {
      if (!audioCtx) return;
      const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
      osc.frequency.value = freq;
      gain.gain.value = 0.15;
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start(); osc.stop(audioCtx.currentTime + ms / 1000);
    }

    function notify(msg) {
      showToast(msg);
      if (window.Notification && Notification.permission === 'granted') {
        new Notification('🚚 Delivery Agent', {body: msg});
      }
    }

    function checkAlerts(stops) {
      const newOnes = stops.filter(s => !knownIds.has(s.id));
      const lateNow = stops.filter(s => s.late && !lateSeen.has(s.id));
      stops.forEach(s => knownIds.add(s.id));
      lateNow.forEach(s => lateSeen.add(s.id));
      if (!alertsReady) { alertsReady = true; return; }   // first load: stay quiet
      if (lateNow.length) {
        beep(440, 400);
        notify(`⏰ LATE: ${lateNow.map(s => s.name).join(', ')}`);
      } else if (newOnes.length) {
        beep(880, 150);
        notify(`📦 New order: ${newOnes.map(s => s.name).join(', ')}`);
      }
    }

    async function refresh() {
      const res = await api('/api/route');
      const data = await res.json();

      // Same data as last time? Only update the clock — no redraw, no flicker.
      const signature = JSON.stringify([data.driver, data.summary, data.stops]);
      const stamp = 'updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
      if (signature === lastSignature) {
        document.getElementById('updated').textContent = stamp;
        return;
      }
      lastSignature = signature;

      checkAlerts(data.stops);
      layer.clearLayers();
      const pts = [[data.driver.lat, data.driver.lng]];
      L.marker([data.driver.lat, data.driver.lng], {icon: L.divIcon({className: '',
        html: '<div class="pin-driver">🚚</div>', iconSize: [36, 36], iconAnchor: [18, 18]}), zIndexOffset: 1000})
        .addTo(layer).bindPopup('🚚 Driver (start)');

      let html = '';
      data.stops.forEach(s => {
        pts.push([s.lat, s.lng]);
        const color = s.late ? '#e5484d' : '#2d6cdf';
        L.marker([s.lat, s.lng], {icon: L.divIcon({className: '',
          html: `<div class="pin" style="background:${color}">${s.sequence}</div>`,
          iconSize: [28, 28], iconAnchor: [14, 14]})})
          .addTo(layer).bindPopup(`${s.sequence}. ${esc(s.name)} — ETA ${s.eta}`);

        let cls = 'stop';
        if (!seenIds.has(s.id)) { cls += ' new'; seenIds.add(s.id); }
        if (s.sequence === 1) cls += ' next';
        if (s.late) cls += ' late';
        const badge = s.late ? '<span class="badge">LATE</span>' : '';
        const promise = s.promise
          ? ` &middot; <span class="promise">promised ${s.promise}</span>` : '';
        html += `<div class="${cls}" data-id="${s.id}">
          <b>${s.sequence}. ${esc(s.name)}</b>${badge}
          <div class="meta">ETA ${s.eta} &middot; +${s.leg_km} km${promise}</div>
          ${s.note ? `<div class="note">📝 ${esc(s.note)}</div>` : ''}
          <div class="actions">
            <a class="nav" href="${navUrl(s)}" target="_blank" rel="noopener">🧭 Navigate</a>
            ${s.phone ? `<a class="nav call" href="tel:${s.phone}">📞 Call</a>` : ''}
            <button class="share" onclick="share('${s.id}')">🔗 Share</button>
            <button onclick="deliver('${s.id}')">✓ Delivered</button>
          </div>
        </div>`;
      });

      document.getElementById('stat-stops').textContent = data.summary.stops;
      document.getElementById('stat-km').textContent = data.summary.total_km;
      document.getElementById('stat-finish').textContent = data.summary.finish;
      document.getElementById('stat-done').textContent = data.summary.delivered;
      document.getElementById('stops').innerHTML = html ||
        '<div class="empty">No orders yet.<br>Tap the map to add one 👆</div>';

      if (pts.length > 1) {
        L.polyline(pts, {color: '#ffffff', weight: 8, opacity: 0.85}).addTo(layer);
        L.polyline(pts, {color: '#2d6cdf', weight: 4, opacity: 0.95}).addTo(layer);
        if (data.stops.length !== lastStopCount) {
          map.fitBounds(pts, {padding: [40, 40]});
        }
      }
      lastStopCount = data.stops.length;
      document.getElementById('updated').textContent =
        'updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
    }

    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, 10000);
    if (window.isSecureContext && navigator.permissions) {
      navigator.permissions.query({name: 'geolocation'})
        .then(p => { if (p.state === 'granted') locateOnce(); })
        .catch(() => {});
    }
  </script>
</body>
</html>
"""

LOGIN_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Login - Delivery Agent</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, "Segoe UI", sans-serif; margin: 0;
      min-height: 100vh; background: #0f1115; color: #e8eaed;
      display: flex; align-items: center; justify-content: center; padding: 20px;
    }
    .card {
      width: 100%; max-width: 360px; background: #1a1d24;
      border: 1px solid #2a2e37; border-radius: 16px; padding: 28px; text-align: center;
    }
    h1 { font-size: 20px; margin: 0 0 4px; }
    .sub { color: #8a8f98; font-size: 13px; margin-bottom: 20px; }
    input {
      width: 100%; padding: 12px; margin: 6px 0; font-size: 16px;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 8px; color: #e8eaed;
    }
    input:focus { outline: none; border-color: #2d6cdf; }
    button {
      width: 100%; padding: 12px; margin-top: 10px; font-size: 15px; font-weight: 600;
      background: #2d6cdf; color: #fff; border: none; border-radius: 8px; cursor: pointer;
    }
    .error {
      color: #ff8a8d; background: rgba(229,72,77,0.12); border: 1px solid #e5484d;
      border-radius: 8px; padding: 8px 10px; font-size: 13px; margin-bottom: 10px;
    }
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Delivery Agent</h1>
    <div class="sub">Enter the dashboard password</div>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <input type="password" name="password" placeholder="Password" autofocus required/>
    <button type="submit">Log in</button>
  </form>
</body>
</html>
"""

ORDERS_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>All orders - Delivery Agent</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, "Segoe UI", sans-serif; margin: 0;
      background: #0f1115; color: #e8eaed;
    }
    .wrap { max-width: 720px; margin: 0 auto; padding: 20px; }
    .top { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
    .top h1 { font-size: 20px; margin: 0; flex: 1; }
    .back {
      font-size: 13px; color: #8a8f98; text-decoration: none;
      border: 1px solid #2a2e37; border-radius: 6px; padding: 6px 10px;
    }
    .back:hover { color: #e8eaed; border-color: #8a8f98; }
    .counts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
    .count { background: #1a1d24; border: 1px solid #2a2e37; border-radius: 10px; padding: 10px; text-align: center; }
    .count .n { font-size: 20px; font-weight: 700; }
    .count .l { font-size: 10px; color: #8a8f98; text-transform: uppercase; letter-spacing: 0.5px; }
    .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
    .tab {
      padding: 7px 12px; font-size: 13px; border-radius: 20px; cursor: pointer;
      background: #1a1d24; border: 1px solid #2a2e37; color: #c3c7cd;
    }
    .tab.on { background: #2d6cdf; border-color: #2d6cdf; color: #fff; }
    input {
      width: 100%; padding: 11px; margin: 4px 0 14px; font-size: 16px;
      background: #1a1d24; border: 1px solid #2a2e37; border-radius: 8px; color: #e8eaed;
    }
    input:focus { outline: none; border-color: #2d6cdf; }
    .order {
      background: #1a1d24; border: 1px solid #2a2e37; border-radius: 10px;
      padding: 12px 14px; margin-bottom: 8px;
    }
    .order.delivered { opacity: 0.7; }
    .row { display: flex; align-items: center; gap: 8px; }
    .row b { flex: 1; font-size: 15px; }
    .badge { font-size: 11px; padding: 3px 8px; border-radius: 20px; font-weight: 600; }
    .badge.pending { background: rgba(240,160,32,0.15); color: #f0a020; }
    .badge.delivered { background: rgba(31,157,85,0.15); color: #1f9d55; }
    .badge.cancelled { background: rgba(229,72,77,0.15); color: #e5484d; }
    .meta { color: #8a8f98; font-size: 12px; margin-top: 4px; }
    .note {
      color: #c3c7cd; font-size: 12px; margin-top: 6px; padding: 6px 8px;
      background: #0f1115; border-left: 3px solid #8a8f98; border-radius: 4px;
    }
    .actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
    .btn {
      font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 6px;
      text-decoration: none; color: #fff; background: #2d6cdf; border: none; cursor: pointer;
    }
    .btn.call { background: #f0a020; color: #0f1115; }
    .btn.share { background: #6b4fd8; }
    .btn.cancel { background: #e5484d; }
    .empty { color: #5a5f68; text-align: center; padding: 30px; font-size: 13px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>📋 All orders</h1>
      <a class="back" href="/">← Dashboard</a>
    </div>

    <div class="counts">
      <div class="count"><div class="n" id="c-all">0</div><div class="l">Total</div></div>
      <div class="count"><div class="n" id="c-pending" style="color:#f0a020">0</div><div class="l">Pending</div></div>
      <div class="count"><div class="n" id="c-delivered" style="color:#1f9d55">0</div><div class="l">Delivered</div></div>
    </div>
    <div class="meta" id="stats" style="margin-bottom:12px"></div>
    <div class="tabs" id="tabs">
      <button class="tab on" data-f="all">All</button>
      <button class="tab" data-f="pending">🟡 Pending</button>
      <button class="tab" data-f="delivered">✅ Delivered</button>
      <button class="tab" data-f="today">📅 Today</button>
    </div>
    <input id="q" placeholder="🔍 Search by name, phone or note…"/>

    <div id="list"></div>
  </div>

  <script>
    let orders = [];
    let filter = 'all';
    let lastSignature = '';

    function esc(t) {
      return String(t).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }

    function navUrl(o) {
      return `https://uri.amap.com/navigation?to=${o.lng},${o.lat},${encodeURIComponent(o.name)}&mode=car&callnative=1`;
    }

    async function share(id) {
      const url = `${location.origin}/track/${id}`;
      try { await navigator.clipboard.writeText(url); alert('Tracking link copied ✅ ' + url); }
      catch (e) { prompt('Copy this tracking link for the customer:', url); }
    }

    async function cancelOrder(id) {
      if (!confirm('Cancel this order?')) return;
      const res = await fetch(`/api/orders/${id}/cancel`, {method: 'POST'});
      if (!res.ok) { alert((await res.json()).error || 'Could not cancel'); return; }
      lastSignature = '';
      load();
    }

    async function editOrder(id) {
      const o = orders.find(x => x.id === id);
      const name = prompt('Customer name:', o.name);
      if (name === null) return;
      const phone = prompt('Phone:', o.phone || '');
      if (phone === null) return;
      const note = prompt('Address / notes:', o.note || '');
      if (note === null) return;
      const res = await fetch(`/api/orders/${id}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, phone, note}),
      });
      if (!res.ok) { alert((await res.json()).error || 'Could not save'); return; }
      lastSignature = '';
      load();
    }

    function render() {
      const q = document.getElementById('q').value.trim().toLowerCase();
      const shown = orders.filter(o => {
        if (filter === 'pending' && o.status !== 'pending') return false;
        if (filter === 'delivered' && o.status !== 'delivered') return false;
        if (filter === 'today' && !o.today) return false;
        if (!q) return true;
        return [o.name, o.phone, o.note].some(v => (v || '').toLowerCase().includes(q));
      });
      const doneToday = orders.filter(o => o.status === 'delivered' && o.today);
      const times = doneToday.map(o => o.minutes).filter(m => m !== null);
      const avg = times.length ? Math.round(times.reduce((a, b) => a + b, 0) / times.length) : null;
      document.getElementById('stats').textContent =
        `📊 Today: ${doneToday.length} delivered` + (avg !== null ? ` · avg ${avg} min per order` : '');
      document.getElementById('c-all').textContent = orders.length;
      document.getElementById('c-pending').textContent = orders.filter(o => o.status === 'pending').length;
      document.getElementById('c-delivered').textContent = orders.filter(o => o.status === 'delivered').length;

      document.getElementById('list').innerHTML = shown.map(o => `
        <div class="order ${o.status}">
          <div class="row">
            <b>${esc(o.name)}</b>
            <span class="badge ${o.status}">${o.status}</span>
          </div>
          <div class="meta">🕒 ${o.created}${o.promise ? ' · promised ' + o.promise : ''}${o.phone ? ' · 📞 ' + esc(o.phone) : ''}</div>
          ${o.note ? `<div class="note">📝 ${esc(o.note)}</div>` : ''}
          <div class="actions">
            <a class="btn" href="${navUrl(o)}" target="_blank" rel="noopener">🧭 Navigate</a>
            ${o.phone ? `<a class="btn call" href="tel:${esc(o.phone)}">📞 Call</a>` : ''}
            <button class="btn share" onclick="share('${o.id}')">🔗 Share</button>
            ${o.status === 'pending' ? `<button class="btn" onclick="editOrder('${o.id}')">✏️ Edit</button>
            <button class="btn cancel" onclick="cancelOrder('${o.id}')">✕ Cancel</button>` : ''}
          </div>
        </div>`).join('') || '<div class="empty">No orders here.</div>';
    }

    async function load() {
      const res = await fetch('/api/orders');
      if (res.status === 401) { location.href = '/login'; return; }
      const data = await res.json();
      const signature = JSON.stringify(data);
      if (signature === lastSignature) return;   // nothing changed: no redraw
      lastSignature = signature;
      orders = data;
      render();
    }

    document.getElementById('tabs').addEventListener('click', e => {
      const btn = e.target.closest('.tab');
      if (!btn) return;
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
      btn.classList.add('on');
      filter = btn.dataset.f;
      render();
    });
    document.getElementById('q').addEventListener('input', render);

    load();
    setInterval(() => { if (!document.hidden) load(); }, 15000);
  </script>
</body>
</html>
"""

TRACK_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Track your order</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, "Segoe UI", sans-serif; margin: 0;
      min-height: 100vh; background: #0f1115; color: #e8eaed;
      display: flex; align-items: center; justify-content: center; padding: 20px;
    }
    .card {
      width: 100%; max-width: 420px; background: #1a1d24;
      border: 1px solid #2a2e37; border-radius: 16px; padding: 28px; text-align: center;
    }
    .logo {
      width: 56px; height: 56px; border-radius: 14px; margin: 0 auto 14px;
      background: linear-gradient(135deg, #2d6cdf, #1f9d55);
      display: flex; align-items: center; justify-content: center; font-size: 28px;
    }
    h1 { font-size: 20px; margin: 0 0 4px; }
    .sub { color: #8a8f98; font-size: 13px; margin-bottom: 22px; }
    .big { font-size: 44px; font-weight: 800; color: #2d6cdf; margin: 6px 0; }
    .label { color: #8a8f98; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
    .row { display: flex; gap: 10px; margin-top: 18px; }
    .box { flex: 1; background: #0f1115; border: 1px solid #2a2e37; border-radius: 12px; padding: 14px; }
    .box .v { font-size: 20px; font-weight: 700; margin-top: 4px; }
    .done { color: #1f9d55; }
    .foot { color: #5a5f68; font-size: 11px; margin-top: 20px; }
  </style>
</head>
<body>
  <div class="card" id="card">
    <div class="logo">🚚</div>
    <h1>Loading…</h1>
  </div>
  <script>
    const orderId = {{ order_id|tojson }};
    function esc(t) {
      return String(t).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }
    async function load() {
      const card = document.getElementById('card');
      try {
        const res = await fetch('/api/track/' + encodeURIComponent(orderId));
        const d = await res.json();
        if (!d.ok) {
          card.innerHTML = '<div class="logo">❓</div><h1>Order not found</h1>' +
            '<div class="sub">Please check your tracking link.</div>';
          return;
        }
        if (d.status === 'delivered') {
          card.innerHTML = `<div class="logo">✅</div><h1 class="done">Delivered!</h1>
            <div class="sub">Enjoy, ${esc(d.name)} 🎉</div>`;
          return;
        }
        if (d.status === 'cancelled') {
          card.innerHTML = '<div class="logo">⛔</div><h1>Order cancelled</h1>';
          return;
        }
        const before = d.stops_before === 0
          ? "You're next! 🎯"
          : `${d.stops_before} stop${d.stops_before === 1 ? '' : 's'} before you`;
        card.innerHTML = `
          <div class="logo">🚚</div>
          <h1>Hi ${esc(d.name)} 👋</h1>
          <div class="sub">Your order is on the way</div>
          <div class="label">Estimated arrival</div>
          <div class="big">${d.eta}</div>
          <div class="row">
            <div class="box"><div class="label">Position</div><div class="v">#${d.position}</div></div>
            <div class="box"><div class="label">Queue</div><div class="v" style="font-size:14px">${before}</div></div>
          </div>
          ${d.promise ? `<div class="sub" style="margin-top:14px">Promised by ${d.promise}</div>` : ''}
          <div class="foot">Updates automatically · ${new Date().toLocaleTimeString()}</div>`;
      } catch (e) {
        card.innerHTML = '<div class="logo">📡</div><h1>Connection problem</h1>' +
          '<div class="sub">Retrying…</div>';
      }
    }
    load();
    setInterval(load, 15000);
  </script>
</body>
</html>
"""

I18N_SCRIPT = r"""<script>
(function () {
  const ZH = {
    "Smart routing dashboard": "智能配送调度台", "Delivery Agent": "智能配送助手",
    "Use my location": "使用我的位置", "Live GPS: off": "实时定位：关", "Live GPS: on": "实时定位：开",
    "Paste a WeChat message here…": "在此粘贴微信消息…", "Handle message": "AI 处理消息",
    "Customer name": "客户姓名", "Phone (optional)": "电话（选填）",
    "Address / notes (optional) — e.g. Bldg 5, floor 3": "地址/备注（选填）— 如 5号楼3层",
    "Tap the map to set the location": "点击地图设置位置", "Latitude": "纬度", "Longitude": "经度",
    "Promise in minutes (optional)": "承诺送达时间（分钟，选填）", "Add order": "添加订单",
    "Stops": "站点", "Finish": "完成时间", "Route": "路线", "updated": "更新于",
    "No orders yet.": "暂无订单。", "Tap the map to add one": "点击地图添加订单",
    "Order added — stop #": "订单已添加 — 第", " in the route": " 站",
    "Navigate": "导航", "Share": "分享", "promised": "承诺", "LATE": "迟到",
    "Logout": "退出", "Orders": "订单",
    "AI inbox": "AI 收件箱", "Retrain AI": "重新训练 AI", "Paste a WeChat message above": "在上方粘贴微信消息",
    "Was I right?": "我判断对了吗？", "It means:": "实际意思：", "Learned!": "已学习！",
    "examples collected": "条训练样本", "press": "点击", "below to use them": "让 AI 学会",
    "% sure": "% 把握", "Customer": "客户", "Driver": "司机",
    "New order": "新订单", "Question": "询问", "Delay": "延误", "Not home": "不在家",
    "Order form filled": "已自动填写订单", "tap the map": "点击地图", "to set the location": "设置位置",
    "Wants to cancel": "想要取消", "Cancel the order": "取消订单", "'s order": " 的订单",
    "is asking about the order": "在询问订单", "Copy reply": "复制回复",
    "Driver delivered": "司机已送达", "Mark as delivered": "标记为已送达",
    "The driver is running late": "司机将会迟到", "is not home": "不在家",
    "Not sure": "不确定", "please check it yourself": "请人工确认",
    "order cancelled": "订单已取消", "order delivered": "订单已送达",
    "Copied — paste it in WeChat": "已复制 — 去微信粘贴", "Training…": "训练中…",
    "AI retrained on": "AI 已重新训练，样本数", "taught by you": "条由你教的",
    "New order:": "新订单：", "Paste a message first.": "请先粘贴一条消息。",
    "Please enter the customer name.": "请输入客户姓名。",
    "Tap the map to set the location, and enter a name.": "请点击地图设置位置并输入姓名。",
    "All orders": "全部订单", "Dashboard": "调度台", "Total": "总数", "Pending": "待配送",
    "Today:": "今天：", "Today": "今天", "All": "全部", "avg": "平均", "min per order": "分钟/单",
    "Search by name, phone or note…": "按姓名、电话或备注搜索…", "Edit": "编辑",
    "No orders here.": "暂无订单。", "pending": "待配送", "cancelled": "已取消",
    "Delivered!": "已送达！", "Delivered": "已送达", "delivered": "已送达",
    "Cancel": "取消", "Right": "对", "Fix": "纠正", "Done": "完成", "Call": "电话", "km": "公里", "Km": "公里",
    "Loading…": "加载中…", "Order not found": "未找到订单",
    "Please check your tracking link.": "请检查您的追踪链接。", "Enjoy,": "请享用，",
    "Order cancelled": "订单已取消", "Hi ": "您好 ", "Your order is on the way": "您的订单正在配送中",
    "Estimated arrival": "预计到达", "Position": "位置", "Queue": "排队",
    "You're next!": "下一个就是您！", "stops before you": "站在您之前", "stop before you": "站在您之前",
    "Promised by": "承诺送达", "Updates automatically": "自动更新",
    "Connection problem": "连接问题", "Retrying…": "重试中…",
    " examples": " 条", "low confidence": "把握不足", "order not found": "未找到订单",
    "unknown intent": "未知意图", "ETA": "预计到达",
    "Clear demo": "清除演示", "demo orders added": "个演示订单已添加",
    "watch the route": "看看路线", "demo orders removed": "个演示订单已删除", "Demo": "演示",
    "Enter the dashboard password": "请输入调度台密码", "Password": "密码", "Log in": "登录",
    "Wrong password.": "密码错误。", "Too many attempts. Wait 10 minutes.": "尝试次数过多，请等待10分钟。"
  };
  const PAIRS = Object.entries(ZH).sort((a, b) => b[0].length - a[0].length);
  const tr = s => { for (const [en, zh] of PAIRS) if (s.includes(en)) s = s.split(en).join(zh); return s; };

  let lang = 'en';
  try { lang = localStorage.getItem('lang') || ((navigator.language || '').startsWith('zh') ? 'zh' : 'en'); } catch (e) {}

  function translate(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const p = n.parentNode && n.parentNode.nodeName;
      if (p === 'SCRIPT' || p === 'STYLE' || p === 'TEXTAREA') continue;
      const t = tr(n.nodeValue);
      if (t !== n.nodeValue) n.nodeValue = t;
    }
    if (root.querySelectorAll) root.querySelectorAll('[placeholder]').forEach(el => {
      const t = tr(el.placeholder); if (t !== el.placeholder) el.placeholder = t;
    });
  }

  function start() {
    const btn = document.createElement('button');
    btn.textContent = lang === 'zh' ? 'EN' : '中文';
    btn.title = 'Language / 语言';
    btn.style.cssText = 'position:fixed;top:10px;right:10px;z-index:10000;width:auto;margin:0;' +
      'padding:6px 12px;font-size:13px;border-radius:20px;border:1px solid #6b4fd8;' +
      'background:#1a1d24;color:#e8eaed;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)';
    btn.onclick = () => {
      try { localStorage.setItem('lang', lang === 'zh' ? 'en' : 'zh'); } catch (e) {}
      location.reload();
    };
    document.body.appendChild(btn);
    if (lang !== 'zh') return;
    document.documentElement.lang = 'zh';
    translate(document.body);
    document.title = tr(document.title);
    new MutationObserver(muts => muts.forEach(m => {
      if (m.type === 'characterData') {
        const t = tr(m.target.nodeValue); if (t !== m.target.nodeValue) m.target.nodeValue = t;
      } else m.addedNodes.forEach(nd => {
        if (nd.nodeType === 3) { const t = tr(nd.nodeValue); if (t !== nd.nodeValue) nd.nodeValue = t; }
        else if (nd.nodeType === 1 && nd.nodeName !== 'TEXTAREA') translate(nd);
      });
    })).observe(document.body, {childList: true, subtree: true, characterData: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
</script>"""

PAGE = PAGE.replace("</body>", I18N_SCRIPT + "\n</body>")
ORDERS_PAGE = ORDERS_PAGE.replace("</body>", I18N_SCRIPT + "\n</body>")
TRACK_PAGE = TRACK_PAGE.replace("</body>", I18N_SCRIPT + "\n</body>")
LOGIN_PAGE = LOGIN_PAGE.replace("</body>", I18N_SCRIPT + "\n</body>")


if __name__ == "__main__":
    app.run(debug=True, port=5000)