"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import statistics
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, render_template,
                   session, redirect, url_for, abort)

from models import Order, Driver, OrderStatus
from geo import GeoService
from optimizer import optimize_route, route_summary
from store import OrderStore
from users import UserStore, public
from limiter import LoginLimiter
from audit import AuditLog
from order_parser import parse_message
from agent import handle_message
import numpy as np
from intent_nn import INTENTS, DATA, MODEL_PATH, predict_intent, train, load_feedback

app = Flask(__name__)

DEV_MODE = os.environ.get("DEV_MODE") == "1"   # demo / simulate tools — only on my Mac


@app.context_processor
def inject_dev_mode():
    """Let every template know if dev tools should be shown."""
    return {"dev_mode": DEV_MODE}

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
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

@app.after_request
def security_headers(resp):
    """🛡️ Extra browser protection on every response."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    return resp

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    DASHBOARD_PASSWORD = secrets.token_urlsafe(8)
    print("WARNING: DASHBOARD_PASSWORD is not set.", flush=True)
    print(f"Temporary password for this run: {DASHBOARD_PASSWORD}", flush=True)

SESSION_VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_version")


def session_version() -> int:
    """🚪 Bump this number to log everyone out."""
    try:
        with open(SESSION_VERSION_PATH) as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0

def current_role():
    """Who is logged in: "owner", "driver" or None."""
    return session.get("role")


def is_logged_in() -> bool:
    if not (session.get("auth") is True
            and current_role() in ("owner", "driver")
            and session.get("v") == session_version()):
        return False
    uid = session.get("uid")
    if uid:                                   # 👤 driver account → blocked = out right now
        user = users.get(uid)
        return bool(user and user["status"] == "approved"
                    and user["pw_version"] == session.get("pv", 0))   # password changed → out
    return current_role() == "owner"          # a driver session must belong to an account


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


def owner_required(view):
    """Only the owner can open this. Drivers are sent to their own page."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_role() == "owner":
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "owner only"}), 403
        return redirect(url_for("driver_page"))
    return wrapper


driver = Driver(
    name="Driver 1",
    lat=float(os.environ.get("DRIVER_LAT", "31.2304")),
    lng=float(os.environ.get("DRIVER_LNG", "121.4737")),
)
geo = GeoService(avg_speed_kmh=driver.avg_speed_kmh)
store = OrderStore()
users = UserStore()


def _asset_version() -> str:
    """🔖 A fingerprint of everything in static/: it changes whenever any file there changes."""
    h = hashlib.sha256()
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    for dirpath, _, files in sorted(os.walk(root)):
        for name in sorted(files):
            with open(os.path.join(dirpath, name), "rb") as f:
                h.update(name.encode())
                h.update(f.read())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()


@app.context_processor
def inject_role():
    """☰ The menu shows owner links or driver links."""
    return {"role": current_role() if session.get("auth") else None}


@app.context_processor
def inject_asset_version():
    return {"asset_v": ASSET_V}
limiter = LoginLimiter()
audit = AuditLog()


def fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "--"


def bad_request(message):
    return jsonify({"ok": False, "error": message}), 400


def driver_for(uid):
    """🚚 This account's driver: its own GPS position, or the default start point."""
    user = users.get(uid) if uid else None
    if user and user.get("lat") is not None:
        return Driver(name=user["name"], lat=user["lat"], lng=user["lng"])
    return driver


def visible_to_me(order) -> bool:
    """👤 The owner sees everything; a driver sees their own orders + the unassigned pool."""
    return current_role() == "owner" or order.driver_id in (None, session.get("uid"))


def my_orders() -> list:
    return [o for o in store.all() if visible_to_me(o)]


def client_ip() -> str:
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


def track_key(order_id: str) -> str:
    """🔑 The secret half of a customer's tracking link. Only this server can make it."""
    secret = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode()
    mac = hmac.new(secret, b"track:" + order_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:16]).decode().rstrip("=")


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
        phone = request.form.get("phone", "").strip()
        if limiter.blocked(ip, phone):
            error = "Too many attempts. Wait 10 minutes."
            audit.add("login_blocked", ip, phone or "owner")
        else:
            given = request.form.get("password", "")
            role, uid = None, None
            if phone:                                 # 👤 driver account
                user = users.check_login(phone, given)
                if user and user["status"] == "approved":
                    role, uid = "driver", user["id"]
                elif user and user["status"] == "pending":
                    error = "Your account is waiting for approval."
                elif user:
                    error = "This account is blocked."
            elif hmac.compare_digest(given.encode(), DASHBOARD_PASSWORD.encode()):
                role = "owner"
            if role:
                limiter.success(phone)
                session.clear()
                session["auth"] = True
                session["role"] = role
                session["v"] = session_version()
                if uid:
                    session["uid"] = uid
                    session["pv"] = users.get(uid)["pw_version"]
                session.permanent = True
                print(f"Login from {ip} as {role}", flush=True)
                audit.add("login", ip, phone or "owner", role)
                return redirect(url_for("home") if role == "owner" else url_for("driver_page"))
            if error is None:                  # pending/blocked = right password, not a guess
                limiter.fail(ip, phone)
                audit.add("login_failed", ip, phone or "owner")
                print(f"Wrong password from {ip}" + (f" for {phone}" if phone else ""), flush=True)
            error = error or "Wrong password."
    return render_template("login.html", error=error)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """👤 Drivers create an account; it waits until the owner approves it."""
    error = done = None
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
        if limiter.signup_blocked(ip):
            error = "Too many new accounts from this network today. Try again tomorrow."
            audit.add("signup_blocked", ip)
        else:
            try:
                user = users.create(request.form.get("phone", ""), request.form.get("name", ""),
                                    request.form.get("password", ""))
                limiter.signup_done(ip)
                audit.add("signup", ip, user["phone"], user["name"])
                print(f"👤 New driver signup: {user['name']} ({user['phone']}) from {ip} — pending",
                      flush=True)
                done = True
            except ValueError as e:
                error = str(e)
    return render_template("signup.html", error=error, done=done)


@app.route("/drivers")
@owner_required
def drivers_page():
    return render_template("drivers.html")


@app.route("/api/drivers")
@owner_required
def list_drivers():
    """👤 Every driver account (never the password hash), pending ones first."""
    return jsonify([public(u) for u in users.all()])


@app.route("/api/drivers/<user_id>/status", methods=["POST"])
@owner_required
def set_driver_status(user_id):
    """✅ Approve or ⛔ block a driver account."""
    status = str((request.get_json(silent=True) or {}).get("status", ""))
    if status not in ("approved", "blocked"):
        return bad_request("status must be approved or blocked")
    if not users.set_status(user_id, status):
        return jsonify({"ok": False}), 404
    print(f"👤 Driver {user_id} → {status}", flush=True)
    u = users.get(user_id)
    audit.add(status, client_ip(), "owner", f"{u['name']} ({u['phone']})" if u else user_id)
    return jsonify({"ok": True})


@app.before_request
def force_password_change():
    """🔑 After an owner reset, the driver can't do anything until they choose a new password."""
    uid = session.get("uid")
    if not uid or request.path in ("/account", "/logout", "/login") or request.path.startswith("/static/"):
        return None
    user = users.get(uid)
    if user and user["must_change"]:
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "change your password first"}), 403
        return redirect(url_for("account"))
    return None


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """🔑 A driver changes their own password."""
    uid = session.get("uid")
    if not uid:                                   # the owner has no account here
        return redirect(url_for("home"))
    user = users.get(uid)
    ip = client_ip()
    error = done = None
    if request.method == "POST":
        if limiter.blocked(ip, user["phone"]):
            error = "Too many attempts. Wait 10 minutes."
        elif not users.check_login(user["phone"], request.form.get("current", "")):
            limiter.fail(ip, user["phone"])
            audit.add("password_change_failed", ip, user["phone"])
            error = "Current password is wrong."
        else:
            try:
                users.set_password(uid, request.form.get("new", ""))
                session["pv"] = users.get(uid)["pw_version"]      # this device stays logged in
                audit.add("password_changed", ip, user["phone"])
                done = True
            except ValueError as e:
                error = str(e)
    user = users.get(uid)
    return render_template("account.html", user=public(user), error=error, done=done,
                           must_change=bool(user["must_change"]))


@app.route("/api/drivers/<user_id>/reset-password", methods=["POST"])
@owner_required
def reset_driver_password(user_id):
    """🔑 Owner gives a driver a temporary password (shown once)."""
    temp = users.reset_password(user_id)
    if not temp:
        return jsonify({"ok": False}), 404
    u = users.get(user_id)
    audit.add("password_reset", client_ip(), "owner", f"{u['name']} ({u['phone']})")
    res = jsonify({"ok": True, "password": temp})
    res.headers["Cache-Control"] = "no-store"     # never keep a password in any cache
    return res


@app.route("/api/security-log")
@owner_required
def security_log():
    """📜 The last 50 security events, newest first."""
    return jsonify(audit.recent(50))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@owner_required
def home():
    return render_template("dashboard.html")


@app.route("/api/route")
@login_required
def api_route():
    mine = my_orders()
    me = driver_for(session.get("uid"))
    route = optimize_route(me, mine, geo)
    summary = route_summary(route)
    late_ids = set(summary.get("late_orders", []))
    delivered = sum(1 for o in mine if o.status == OrderStatus.DELIVERED)
    data = {
        "driver": {"lat": me.lat, "lng": me.lng, "name": me.name},
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
                "track_key": track_key(o.id),
                "driver_id": o.driver_id,
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
    if current_role() == "owner":                 # 🗺️ every driver on the owner's map
        now = time.time()
        data["drivers"] = [
            {"id": u["id"], "name": u["name"], "lat": u["lat"], "lng": u["lng"],
             "age": round(now - (u["located_at"] or 0))}
            for u in users.all() if u["status"] == "approved" and u.get("lat") is not None]
    return jsonify(data)


@app.route("/api/orders", methods=["POST"])
@owner_required
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
    if order and visible_to_me(order):            # 🛡️ never someone else's order (IDOR)
        if order.status != OrderStatus.PENDING:
            return bad_request("order is not open")
        if order.driver_id is None and session.get("uid"):
            order.driver_id = session["uid"]      # a pool order belongs to whoever delivers it
        order.status = OrderStatus.DELIVERED
        order.delivered_at = time.time()
        store.save()
        print(f"✅ Delivered: {order.customer_name}", flush=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

@app.route("/api/orders/<order_id>/assign", methods=["POST"])
@owner_required
def assign_order(order_id):
    """👤 Give an order to a driver, or back to the pool (driver_id = null)."""
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    driver_id = (request.get_json(silent=True) or {}).get("driver_id")
    if driver_id is not None:
        u = users.get(str(driver_id))
        if not u or u["status"] != "approved":
            return bad_request("unknown or not approved driver")
        driver_id = u["id"]
    order.driver_id = driver_id
    store.save()
    print(f"👤 Assigned: {order.customer_name} → {driver_id or 'pool'}", flush=True)
    return jsonify({"ok": True})


@app.route("/api/orders/<order_id>/cancel", methods=["POST"])
@owner_required
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
@owner_required
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
@owner_required
def parse_order_text():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return bad_request("text is required")
    if len(text) > 500:
        return bad_request("text is too long")
    return jsonify(parse_message(text))
@app.route("/api/agent", methods=["POST"])
@owner_required
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
@owner_required
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
    real = len(load_feedback(include_synthetic=False))
    return len(examples), real, len(examples) - len(DATA) - real


if os.environ.get("SKIP_AUTO_RETRAIN") != "1":
    try:   # on every start/deploy, include the feedback collected on this server
        _n, _fb, _syn = retrain_model()
        print(f"🧠 Intent model ready: {_n} examples ({_fb} real feedback + {_syn} synthetic)", flush=True)
    except Exception as e:
        print(f"⚠️ Could not retrain at start: {e}", flush=True)


@app.route("/api/retrain", methods=["POST"])
@owner_required
def retrain():
    total, from_feedback, synthetic = retrain_model()
    print(f"🧠 Retrained: {total} examples ({from_feedback} real feedback + {synthetic} synthetic)", flush=True)
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
@owner_required
def demo_orders():
    """✨ Add realistic demo orders around the driver (great for showing the app)."""
    if not DEV_MODE:
        abort(404)
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
@owner_required
def clear_demo():
    """🧹 Remove every demo order (real orders are never touched)."""
    if not DEV_MODE:
        abort(404)
    before = len(store.orders)
    store.orders = [o for o in store.orders if not (o.note or "").startswith(DEMO_TAG)]
    store.save()
    removed = before - len(store.orders)
    print(f"🧹 Demo: removed {removed} orders", flush=True)
    return jsonify({"ok": True, "removed": removed})

@app.route("/api/stats")
@owner_required
def api_stats():
    """📊 Numbers for the stats page."""
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    per_day = {d: 0 for d in days}
    minutes = []
    by_hour = [0] * 24
    on_time = late = 0
    per_driver = {}                               # driver id → their numbers (same rules as above)

    for o in store.all():
        if o.status != OrderStatus.DELIVERED or not o.delivered_at:
            continue
        done = datetime.fromtimestamp(o.delivered_at)
        p = per_driver.setdefault(o.driver_id, {"today": 0, "week": 0, "minutes": [], "on_time": 0, "late": 0})
        p["today"] += done.date() == today
        p["week"] += done.date() in per_day
        if o.created_at:
            p["minutes"].append((o.delivered_at - o.created_at) / 60)
        if o.promised_by:
            p["on_time" if o.delivered_at <= o.promised_by else "late"] += 1
        if done.date() in per_day:
            per_day[done.date()] += 1
        by_hour[done.hour] += 1
        if o.created_at:
            minutes.append((o.delivered_at - o.created_at) / 60)
        if o.promised_by:
            if o.delivered_at <= o.promised_by:
                on_time += 1
            else:
                late += 1

    promised = on_time + late
    names = {u["id"]: u["name"] for u in users.all()}
    drivers = sorted((
        {"name": names.get(d, "Deleted account") if d else "No driver",
         "today": p["today"],
         "week": p["week"],
         "median_minutes": round(statistics.median(p["minutes"])) if p["minutes"] else None,
         "on_time_pct": round(p["on_time"] / (p["on_time"] + p["late"]) * 100)
                        if p["on_time"] + p["late"] else None,
         "late": p["late"]}
        for d, p in per_driver.items()), key=lambda r: -r["week"])
    return jsonify({
        "drivers": drivers,
        "days": [d.strftime("%m-%d") for d in days],
        "delivered": [per_day[d] for d in days],
        "by_hour": by_hour,
        "total_delivered": sum(by_hour),
        "avg_minutes": round(statistics.median(minutes)) if minutes else None,
        "on_time_pct": round(on_time / promised * 100) if promised else None,
    })

@app.route("/api/logout-all", methods=["POST"])
@owner_required
def logout_all():
    """🚪 Log out everyone (owner + all drivers) right now."""
    audit.add("logout_all", client_ip(), "owner")
    new = session_version() + 1
    with open(SESSION_VERSION_PATH, "w") as f:
        f.write(str(new))
    session.clear()
    print(f"🚪 Logged everyone out (session version {new})", flush=True)
    return jsonify({"ok": True})

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
    uid = session.get("uid")
    if uid:
        users.set_location(uid, lat, lng)        # 📍 this driver's own position
    else:
        driver.lat, driver.lng = lat, lng        # the owner moves the default start point
    return jsonify({"ok": True, "lat": lat, "lng": lng})


@app.route("/orders")
@owner_required
def orders_page():
    return render_template("orders.html")


@app.route("/stats")
@owner_required
def stats_page():
    return render_template("stats.html")

@app.route("/api/orders")
@owner_required
def list_orders():
    """Every order, newest first — delivered ones included."""
    today = datetime.now().date()
    items = []
    for o in sorted(store.all(), key=lambda x: x.created_at or 0, reverse=True):
        created = datetime.fromtimestamp(o.created_at) if o.created_at else None
        items.append({
            "id": o.id,
            "track_key": track_key(o.id),
            "driver_id": o.driver_id,
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
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "error": "order not found"}), 404
    if order.status == OrderStatus.DELIVERED:
        return jsonify({"ok": True, "status": "delivered", "name": order.customer_name})
    if order.status == OrderStatus.CANCELLED:
        return jsonify({"ok": True, "status": "cancelled", "name": order.customer_name})

    # Where is this order in the current route?
    same_driver = [o for o in store.all() if o.driver_id == order.driver_id]
    route = optimize_route(driver_for(order.driver_id), same_driver, geo)
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
    return render_template("track.html", order_id=order_id)


@app.route("/driver")
@login_required
def driver_page():
    return render_template("driver.html", role=current_role())


if __name__ == "__main__":
    app.run(debug=True, port=5000)