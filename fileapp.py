"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pyotp
from werkzeug.security import check_password_hash, generate_password_hash
import os
import re
import secrets
import statistics
import time
from datetime import datetime, timedelta

# The shop runs on China time, whatever the server's own clock zone is (Render uses UTC).
os.environ["TZ"] = os.environ.get("SHOP_TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()
from functools import wraps

from flask import (Flask, request, jsonify, render_template,
                   session, redirect, url_for, abort, send_from_directory, has_request_context)

from models import Order, Driver, OrderStatus
from geo import GeoService
from optimizer import optimize_route, route_summary
from store import OrderStore
from users import UserStore, public, find_nearest_driver
from limiter import LoginLimiter
from audit import AuditLog
from order_parser import parse_message
from agent import handle_message
import owner_ai
import numpy as np
from intent_nn import INTENTS, DATA, MODEL_PATH, predict_intent, train, load_feedback

app = Flask(__name__)


# Behind Render's proxy every request comes from the proxy itself; the real
# visitor IP is the last entry Render adds to X-Forwarded-For. ProxyFix reads
# only that entry, so a visitor cannot fake it with their own headers.
BEHIND_PROXY = os.environ.get("BEHIND_PROXY") == "1"
if BEHIND_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app,
                            x_for=int(os.environ.get("PROXY_HOPS", "1")),
                            x_proto=1, x_host=1)


def _visitor_ip() -> str:
    """Client IP for rate limits and the security log."""
    if BEHIND_PROXY:
        return request.remote_addr or "?"        # already fixed by ProxyFix
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"
from flask_compress import Compress  # noqa: E402
Compress(app)   # ⚡ gzip/brotli: smaller pages on slow networks

import alerts as _alerts_mod  # noqa: E402
import error_alerts  # noqa: E402
error_alerts.install(app, _alerts_mod._telegram)   # 🚨 unhandled errors → owner on Telegram

DEV_MODE = os.environ.get("DEV_MODE", "1") == "1"   # set DEV_MODE=0 to hide demo tools   # demo / simulate tools — only on my Mac


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

# 📷 Delivery proof photos live OUTSIDE static/, so nginx can't serve them directly.
# They are served only through a login-protected endpoint (see /api/orders/<id>/proof).
HERE = os.path.dirname(os.path.abspath(__file__))
PROOF_DIR = os.environ.get("PROOF_DIR", os.path.join(HERE, "proofs"))
os.makedirs(PROOF_DIR, exist_ok=True)
MAX_PROOF_BYTES = 5 * 1024 * 1024          # 5 MB per photo
ALLOWED_PROOF_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

from flask import Response as _PhotoResponse
from photos import PhotoStore

_photos = None


def photo_store() -> PhotoStore:
    """Photo table in the same database as the orders (created on first use)."""
    global _photos
    if _photos is None:
        _photos = PhotoStore(getattr(store, "path", os.path.join(HERE, "orders.db")))
    return _photos


def proof_response(name: str):
    """Serve a delivery photo from the database; older photos still come from disk."""
    found = photo_store().get(name)
    if found is None:
        # send_from_directory is safe: it refuses paths that escape PROOF_DIR.
        return send_from_directory(PROOF_DIR, name, max_age=0)
    data, mime = found
    resp = _PhotoResponse(data, mimetype=mime)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# 👀 In demo (viewer) sessions, every page gets a banner, controls a visitor can't
# use are hidden, and writes outside the demo sandbox are stopped in the browser
# with a clear message, instead of buttons that silently do nothing.
VIEWER_SNIPPET = """
<style>
  #demo-banner { position: fixed; top: 0; left: 0; right: 0; z-index: 99999;
    background: #f0a020; color: #111; text-align: center; padding: 6px 10px;
    font: 600 13px/1.4 system-ui, sans-serif; }
  body { padding-top: 30px !important; }
  #demo-toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    z-index: 99999; background: #222; color: #fff; padding: 10px 16px; border-radius: 10px;
    font: 14px system-ui, sans-serif; display: none; }
  /* Discreet badge instead of a full-width bar, so the demo never hides the app */
  #demo-banner { top: auto !important; right: auto !important; left: 12px !important;
    bottom: calc(12px + env(safe-area-inset-bottom)) !important; width: auto !important;
    padding: 5px 11px !important; border-radius: 999px !important; font-size: 0 !important;
    opacity: .92; pointer-events: auto; cursor: pointer; text-decoration: none;
    box-shadow: 0 4px 14px rgba(0, 0, 0, .25); }
  #demo-banner::after { content: '演示 DEMO · 退出 Exit ✕'; font-size: 11px; font-weight: 700; letter-spacing: .04em; }
  #demo-banner:active { transform: scale(.96); }
  body { padding-top: 0 !important; }
  /* Hide every control a demo visitor can't use */
  #log,
  button[onclick*="retrain" i],
  button[onclick*="logoutAll"], body:has(#log) #list button { display: none !important; }
</style>
<a id="demo-banner" href="/try/exit" title="Leave the demo">Demo · sample data · 演示数据</a>
<div id="demo-toast">Not available in the demo</div>
<script>
(function () {
  function toast() {
    var t = document.getElementById('demo-toast');
    t.style.display = 'block';
    clearTimeout(t._h); t._h = setTimeout(function () { t.style.display = 'none'; }, 2200);
  }
  // Stop every write before it reaches the server.
  var realFetch = window.fetch;
  window.fetch = function (url, opts) {
    var method = ((opts && opts.method) || 'GET').toUpperCase();
    var path = String(url).split('?')[0];
    var ok = ['/api/agent', '/api/parse', '/api/orders', '/api/demo', '/api/demo/clear', '/api/driver/location'].indexOf(path) !== -1 || (path.indexOf('/api/orders/') === 0 && (path.slice(-8) === '/deliver' || path.slice(-5) === '/paid'))
             || path.indexOf('/api/orders/') === 0;
    if (method !== 'GET' && !ok && path.indexOf('/logout') === -1) {
      toast();
      return Promise.resolve(new Response(JSON.stringify({ok: false, error: 'demo'}),
        {status: 403, headers: {'Content-Type': 'application/json'}}));
    }
    return realFetch.apply(this, arguments);
  };
  // Lock text fields so nobody types into a form that can't be sent.
  function lock() {
    document.querySelectorAll('textarea, input:not([type=hidden])').forEach(function (el) {
      el.readOnly = true;
    });
  }
  window.alert = function () { toast(); };
  // The "Log out everyone" card and the security-log heading go too.
  function tidy() {
    var b = document.querySelector('[onclick="logoutAll()"]');
    var card = b && b.closest('div');
    while (card && !card.querySelector('.name')) card = card.parentElement && card.parentElement.closest('div');
    if (card) card.style.display = 'none';
    var log = document.getElementById('log');
    var head = log && log.previousElementSibling;
    if (head && /security/i.test(head.textContent)) head.style.display = 'none';
  }
  document.addEventListener('DOMContentLoaded', tidy);
  tidy();
})();
</script>
"""


DEMO_EXIT_SNIPPET = VIEWER_SNIPPET.split("<div id=\"demo-toast\">")[0] \
    .split("/* Hide every control")[0] + "</style>\n" \
    + '<a id="demo-banner" href="/try/exit" title="Leave the demo">Demo</a>\n'


@app.after_request
def viewer_overlay(resp):
    if resp.mimetype != "text/html" or resp.direct_passthrough:
        return resp
    if session.get("role") == "viewer":
        snippet = VIEWER_SNIPPET
    elif session.get("demo"):
        snippet = DEMO_EXIT_SNIPPET          # demo customer: exit pill only
    else:
        return resp
    html = resp.get_data(as_text=True)
    if "</body>" in html and "demo-banner" not in html:
        resp.set_data(html.replace("</body>", snippet + "</body>", 1))
    return resp


@app.after_request
def security_headers(resp):
    """🛡️ Extra browser protection on every response."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    # ⚡ Static files with ?v=... never change (a new version gets a new ?v=), so browsers keep them a year.
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                         if request.args.get("v") else "public, max-age=3600")
    # HTML is always revalidated, so nobody is stuck on an old copy of a page.
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        # TODO: drop 'unsafe-inline' once the inline JS is moved into files
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://server.arcgisonline.com https://*.is.autonavi.com https://tm.amap.com; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    return resp

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    DASHBOARD_PASSWORD = secrets.token_urlsafe(8)
    print("WARNING: DASHBOARD_PASSWORD is not set.", flush=True)
    print(f"Temporary password for this run: {DASHBOARD_PASSWORD}", flush=True)

SESSION_VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_version")

# 🔐 Two-factor for the owner account. Optional: if the file is missing,
# login works exactly as before, so you can never lock yourself out by accident.
TWOFA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".owner_2fa")


def twofa_load() -> dict:
    """Stored 2FA state: {"secret": str, "recovery": [hashed codes]} or {} if off."""
    try:
        with open(TWOFA_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def twofa_save(data: dict) -> None:
    """Write 2FA state, readable only by this user."""
    with open(TWOFA_PATH, "w") as f:
        json.dump(data, f)
    filesync.persist(TWOFA_PATH)
    os.chmod(TWOFA_PATH, 0o600)


def twofa_enabled() -> bool:
    return bool(twofa_load().get("secret"))


def twofa_check(code: str) -> bool:
    """True if the code is a valid TOTP, or an unused recovery code (burned on use)."""
    data = twofa_load()
    secret = data.get("secret")
    if not secret:
        return True                      # 2FA is off: nothing to check
    code = code.strip().replace(" ", "")
    # valid_window=1 tolerates a clock that is one 30s step out of sync
    if pyotp.TOTP(secret).verify(code, valid_window=1):
        return True
    # Recovery codes are stored hashed and each one works only once.
    for stored in list(data.get("recovery", [])):
        if check_password_hash(stored, code):
            data["recovery"].remove(stored)
            twofa_save(data)
            audit.add("2fa_recovery_used", client_ip(), "owner")
            return True
    return False


# 🔴 Emergency kill switch. One signed link from your phone changes this file,
# and every request checks it. Modes: normal | readonly | lockdown | off
PANIC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".panic_mode")
PANIC_MODES = ("normal", "readonly", "lockdown", "off")

# On Render, bring the state files back from the database (no-op locally).
import filesync
filesync.restore([SESSION_VERSION_PATH, TWOFA_PATH, PANIC_PATH])


def panic_mode() -> str:
    """Current emergency mode (defaults to normal if the file is missing)."""
    try:
        with open(PANIC_PATH) as f:
            mode = f.read().strip()
        return mode if mode in PANIC_MODES else "normal"
    except OSError:
        return "normal"


def set_panic_mode(mode: str) -> None:
    """Write the new mode. Called only by the signed /panic link."""
    with open(PANIC_PATH, "w") as f:
        f.write(mode)


    filesync.persist(PANIC_PATH)
def panic_key(mode: str) -> str:
    """Signature for one mode, so only links you generated can switch it."""
    secret = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode()
    mac = hmac.new(secret, b"panic:" + mode.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac)[:22].decode()


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
            and current_role() in ("owner", "driver", "customer", "viewer")
            and session.get("v") == session_version()):
        return False
    uid = session.get("uid")
    if uid:                                   # 👤 driver account → blocked = out right now
        user = users.get(uid)
        return bool(user and user["status"] == "approved" and user["role"] == current_role()
                    and user["pw_version"] == session.get("pv", 0))   # password changed → out
    return current_role() in ("owner", "viewer")          # a driver session must belong to an account


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
        if current_role() in ("owner", "viewer"):
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "owner only"}), 403
        return redirect(url_for("driver_page"))
    return wrapper


driver = Driver(
    name="Driver 1",
    lat=float(os.environ.get("DRIVER_LAT", "30.257")),
    lng=float(os.environ.get("DRIVER_LNG", "119.727")),
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
    here = session.get("here") if has_request_context() and not uid else None
    if here:                                     # owner / demo visitor: this browser's own GPS
        return Driver(name=driver.name, lat=here[0], lng=here[1])
    return driver


DEMO_FOLLOW_KM = 5    # GPS this far from today's demo orders: move them next to the visitor


def _demo_center() -> tuple:
    """Where demo orders go: this browser's GPS position, else the default start point."""
    here = session.get("here") if has_request_context() else None
    return (here[0], here[1]) if here else (driver.lat, driver.lng)


def _move_demo_near(lat: float, lng: float) -> None:
    """A visitor far from today's demo orders gets them rebuilt in their own city."""
    from geo import haversine_km
    live = [o for o in store.all()
            if (o.note or "").startswith(DEMO_TAG) and o.status == OrderStatus.PENDING]
    if live:
        c_lat = sum(o.lat for o in live) / len(live)
        c_lng = sum(o.lng for o in live) / len(live)
        if haversine_km(lat, lng, c_lat, c_lng) < DEMO_FOLLOW_KM:
            return
        store.orders = [o for o in store.orders
                        if not ((o.note or "").startswith(DEMO_TAG) and o.status == OrderStatus.PENDING)]
        store.save()
    seed_demo_orders(center=(lat, lng))


def visible_to_me(order) -> bool:
    """👤 The owner sees everything; a driver sees their own orders + the unassigned pool."""
    if current_role() == "viewer":
        return (order.note or "").startswith(DEMO_TAG)
    return current_role() == "owner" or order.driver_id in (None, session.get("uid"))


def my_orders() -> list:
    return [o for o in orders_for_request() if visible_to_me(o)]


def client_ip() -> str:
    return _visitor_ip()


def track_key(order_id: str) -> str:
    """🔑 The secret half of a customer's tracking link. Only this server can make it."""
    secret = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode()
    mac = hmac.new(secret, b"track:" + order_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:16]).decode().rstrip("=")


@app.route("/me")
@login_required
def my_page():
    """🧾 The customer's own dashboard: their orders on a map."""
    if current_role() != "customer":
        return redirect(url_for("home"))
    me = users.get(session.get("uid")) or {}
    return render_template("me.html", name=me.get("name", ""))


def _order_items(order) -> list:
    """The product lines of a customer order ([] for orders typed in by the shop)."""
    try:
        items = json.loads(order.items or "[]")
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def customer_order_view(order) -> dict:
    """What a customer sees about one of their orders."""
    return {"id": order.id, "name": order.customer_name, "note": public_note(order),
            "status": order.status.value, "created_at": order.created_at,
            "lat": order.lat, "lng": order.lng, "price": order.price, "items": _order_items(order),
            "has_trail": order.status == OrderStatus.DELIVERED and bool(order.trail),
            "track_url": f"/track/{order.id}?k={track_key(order.id)}"}


@app.route("/api/me/orders")
@login_required
def my_orders_api():
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    orders = [store.get(i) for i in users.customer_order_ids(session["uid"])]
    return jsonify({"ok": True, "orders": [customer_order_view(o) for o in orders if o]})


@app.route("/api/me/orders/<order_id>/trail")
@login_required
def my_order_trail(order_id):
    """The path the driver took for one of MY delivered orders. Any other order: the same 404."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    o = store.get(order_id) if order_id in users.customer_order_ids(session["uid"]) else None
    if not o or o.status != OrderStatus.DELIVERED:
        return jsonify({"ok": False, "error": "order not found"}), 404
    try:
        trail = json.loads(o.trail) if o.trail else []
    except ValueError:
        trail = []
    return jsonify({"ok": True, "id": o.id, "lat": o.lat, "lng": o.lng,
                    "delivered_at": o.delivered_at, "trail": trail})


@app.route("/api/me/claim", methods=["POST"])
@login_required
def claim_order():
    """Add an order to my account with its tracking link (the signed key proves it's mine)."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    link = (request.get_json(silent=True) or {}).get("link", "")
    m = re.search(r"/track/([A-Za-z0-9]+)\?k=([A-Za-z0-9_\-]+)", link)
    if not m or not store.get(m.group(1)) or \
            not hmac.compare_digest(m.group(2).encode(), track_key(m.group(1)).encode()):
        return jsonify({"ok": False, "error": "this tracking link is not valid"}), 400
    users.link_order(session["uid"], m.group(1))
    return jsonify({"ok": True})


MAX_OPEN_REQUESTS = 3          # orders a customer can have waiting for the shop at once
MAX_REQUESTS_PER_HOUR = 5      # orders a customer can place per hour (anti-spam)
MAX_ADDRESS = 120


def _notify_new_request(order, summary: str) -> None:
    """Telegram ping for the owner, sent in the background so the customer never waits."""
    import threading
    import alerts
    text = (f"New customer order: {order.customer_name} ({order.phone})\n"
            f"{summary}\nTotal: {order.price} yuan\nOpen the Orders page to confirm.")
    threading.Thread(target=alerts._telegram, args=(text,), daemon=True).start()


def catalog_items(raw_items):
    """(items, subtotal, error) for a cart from the browser. Only active products, whole
    quantities; name, unit and price come from the catalog, never from the browser.
    Items are saved as JSON [{id, name, unit, price, qty}], the format stats read."""
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 20:
        return None, 0.0, "choose between 1 and 20 products"
    catalog = {p["id"]: p for p in product_store().all()}      # active products only
    items, total = [], 0.0
    for it in raw_items:
        p = catalog.get(str(it.get("id", ""))) if isinstance(it, dict) else None
        if not p:
            return None, 0.0, "a product is not available any more"
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            return None, 0.0, "quantity must be a whole number"
        if not 1 <= qty <= 99:
            return None, 0.0, "quantity must be between 1 and 99"
        items.append({"id": p["id"], "name": p["name"], "unit": p["unit"], "price": p["price"], "qty": qty})
        total += p["price"] * qty
    return items, total, None


@app.route("/api/me/order", methods=["POST"])
@login_required
def customer_place_order():
    """A customer orders from the catalog. The server computes the total; the shop confirms it."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    uid = session["uid"]
    me = users.get(uid) or {}
    body = request.get_json(silent=True) or {}

    # 1) Items: visible products and whole quantities, priced from the catalog.
    items, total, error = catalog_items(body.get("items"))
    if error:
        return bad_request(error)

    # 1b) Shop rules: minimum order and delivery fee (never trusted from the browser).
    shop = settings_store().all()
    if total < shop["min_order"]:
        return bad_request(f"minimum order is {shop['min_order']:g} yuan")
    fee = delivery_fee_for(total, shop)

    # 2) Where: a point on the map plus building / floor details.
    try:
        lat, lng = float(body.get("lat")), float(body.get("lng"))
    except (TypeError, ValueError):
        return bad_request("tap the map to set your location")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):      # also rejects nan
        return bad_request("tap the map to set your location")
    address = str(body.get("address", "")).strip()
    if not address:
        return bad_request("add your address details (building, floor, room)")
    if len(address) > MAX_ADDRESS:
        return bad_request(f"address is too long (max {MAX_ADDRESS} characters)")

    # 3) Anti-spam: a few waiting orders at most, and a few per hour.
    now = time.time()
    mine = [o for o in (store.get(i) for i in users.customer_order_ids(uid)) if o]
    if sum(1 for o in mine if o.status == OrderStatus.REQUESTED) >= MAX_OPEN_REQUESTS:
        return bad_request("you already have orders waiting for the shop")
    if sum(1 for o in mine if (o.created_at or 0) > now - 3600) >= MAX_REQUESTS_PER_HOUR:
        return jsonify({"ok": False, "error": "too many orders, please try again later"}), 429

    demo = bool(session.get("demo"))     # demo customer: the order stays in the demo sandbox
    summary = ", ".join(f"{i['name']} x{i['qty']}" for i in items)
    if fee:
        summary += f", delivery {fee:g}"
    lat, lng, _ = _snap_to_learned(me.get("phone", ""), lat, lng)
    order = Order(customer_name=me.get("name") or "Customer", lat=lat, lng=lng,
                  phone=me.get("phone", ""), note=((DEMO_TAG if demo else "") + f"{address} | {summary}")[:200],
                  price=round(total + fee, 2), status=OrderStatus.PENDING if demo else OrderStatus.REQUESTED,
                  items=json.dumps(items, ensure_ascii=False))
    _store_add(order)
    users.link_order(uid, order.id)
    if not demo:
        _notify_new_request(order, summary)   # demo orders never ping the shop
    return jsonify({"ok": True, "id": order.id, "total": order.price, "fee": fee,
                    "track_url": f"/track/{order.id}?k={track_key(order.id)}"})


@app.route("/api/me/ai-order", methods=["POST"])
@login_required
def customer_ai_order():
    """Read a customer's message ("两桶水送到5号楼302") into a cart suggestion.
    Nothing is ordered here: the page shows the cart and the customer sends it as usual."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    if not text:
        return bad_request("text is required")
    if len(text) > 300:
        return bad_request("text is too long")
    from order_parser import match_products
    catalog = product_store().all()
    found = match_products(text, catalog)
    by_id = {p["id"]: p for p in catalog}
    items = [{**{k: by_id[i["id"]][k] for k in ("id", "name", "unit", "price")}, "qty": i["qty"]}
             for i in found["items"]]
    address = (found["address"] or "")[:MAX_ADDRESS]
    # Reply language follows the message: Chinese characters → Chinese, anything else → English
    return jsonify({"ok": True, "items": items, "address": address, "unmatched": found["unmatched"],
                    "lang": owner_ai.reply_lang(text)})


@app.route("/api/me/orders/<order_id>/cancel", methods=["POST"])
@login_required
def customer_cancel_order(order_id):
    """A customer can take back an order while the shop has not confirmed it yet."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    order = store.get(order_id)
    if not order or order_id not in users.customer_order_ids(session["uid"]):
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.REQUESTED:
        return bad_request("the shop already confirmed this order, please contact them")
    order.status = OrderStatus.CANCELLED
    store.save()
    return jsonify({"ok": True})


@app.route("/api/orders/<order_id>/confirm", methods=["POST"])
@owner_required
def confirm_order(order_id):
    """The shop accepts a customer's order: it joins the route like any other order."""
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.REQUESTED:
        return bad_request("this order is not waiting for confirmation")
    order.status = OrderStatus.PENDING
    nearest = find_nearest_driver(users, order.lat, order.lng)
    if nearest and not order.driver_id:
        order.driver_id = nearest["id"]
    store.save()
    return jsonify({"ok": True})


import requests  # noqa: E402
from collections import OrderedDict  # noqa: E402
from geo import wgs84_to_gcj02, gcj02_to_wgs84  # noqa: E402

_ROAD_CACHE: "OrderedDict[str, list]" = OrderedDict()


@app.route("/api/road")
@login_required
def road_path():
    """🛣️ The road between points, so routes follow streets instead of straight lines.
    Points come and go in GCJ-02 (Gaode); OSRM works in WGS-84. Cached. If OSRM is slow
    or down we answer the straight line, so the map never breaks."""
    try:
        pts = [tuple(float(v) for v in p.split(",")) for p in request.args.get("pts", "").split(";") if p]
    except ValueError:
        return bad_request("pts must look like lat,lng;lat,lng")
    if not 2 <= len(pts) <= 25 or any(len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180) for p in pts):
        return bad_request("send 2 to 25 valid points")
    key = ";".join(f"{a:.5f},{b:.5f}" for a, b in pts)
    if key in _ROAD_CACHE:
        _ROAD_CACHE.move_to_end(key)
        return jsonify({"ok": True, "path": _ROAD_CACHE[key], "road": True})
    coords = ";".join("{1:.6f},{0:.6f}".format(*gcj02_to_wgs84(a, b)) for a, b in pts)
    try:
        r = requests.get(f"https://router.project-osrm.org/route/v1/driving/{coords}",
                         params={"overview": "full", "geometries": "geojson"}, timeout=4)
        line = r.json()["routes"][0]["geometry"]["coordinates"]       # [[lng, lat], ...] in WGS-84
        path = [[round(v, 6) for v in wgs84_to_gcj02(lat, lng)] for lng, lat in line]
    except Exception:
        return jsonify({"ok": True, "path": [list(p) for p in pts], "road": False})
    _ROAD_CACHE[key] = path
    if len(_ROAD_CACHE) > 500:
        _ROAD_CACHE.popitem(last=False)
    return jsonify({"ok": True, "path": path, "road": True})


@app.route("/api/customers/quiet")
@owner_required
def quiet_customers():
    """🧠 Regulars who went quiet (usually every ~7 days, nothing for 14): bring them back."""
    import customers
    return jsonify({"ok": True, "customers": customers.quiet(orders_for_request(), time.time())[:10]})


@app.route("/drivers")
@owner_required
def drivers_page():
    return render_template("drivers.html")


@app.route("/api/drivers")
@owner_required
def list_drivers():
    """👤 Every driver account (never the password hash), pending ones first."""
    # Collect every rating per driver in one pass.
    ratings: dict[str, list[int]] = {}
    for o in orders_for_request():
        if o.driver_id and o.rating:
            ratings.setdefault(o.driver_id, []).append(o.rating)

    result = []
    for u in users.all():
        row = public(u)
        if session.get("role") == "viewer" and row.get("phone"):
            row["phone"] = row["phone"][:3] + "****" + row["phone"][-4:]
        if session.get("role") == "viewer":
            for k in ("lat", "lng", "located_at"):   # the public demo never shows a real driver's position
                row.pop(k, None)
        rs = ratings.get(u["id"], [])
        row["rated_count"] = len(rs)
        row["avg_rating"] = round(sum(rs) / len(rs), 1) if rs else None
        result.append(row)
    return jsonify(result)


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


# 👀 "Viewer" sessions for demos: a sandbox where visitors can try the AI and play
# with demo orders, but never see real data, real accounts or the security pages.
VIEWER_BLOCKED = ("/security", "/api/security-log", "/account")


def demo_key() -> str:
    secret = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode()
    mac = hmac.new(secret, b"demo-viewer", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac)[:22].decode()


VIEWER_WRITES = ("/api/agent", "/api/parse", "/api/orders", "/api/demo", "/api/demo/clear",
                 "/api/driver/location")


def _store_add(order):
    """Every new order goes through here. A demo visitor's order is always a demo
    order and is never assigned to a real driver."""
    if has_request_context() and session.get("role") == "viewer":
        if not (order.note or "").startswith(DEMO_TAG):
            order.note = DEMO_TAG + (order.note or "")
        order.driver_id = None
    return store.add(order)


def _viewer_may_write() -> bool:
    """Demo visitors may try the AI and play with orders, but only demo orders."""
    if request.path in VIEWER_WRITES:
        return True
    parts = request.path.strip("/").split("/")      # api / orders / <id> [/ deliver|cancel]
    if parts[:2] == ["api", "orders"] and (len(parts) == 3 or (len(parts) == 4 and parts[3] in ("deliver", "cancel", "paid"))):
        order = store.get(parts[2])
        return bool(order and (order.note or "").startswith(DEMO_TAG))
    return False


CUSTOMER_ALLOWED = ("/me", "/try/", "/api/me/", "/api/products", "/track/", "/api/track/", "/static/", "/logout", "/manifest.webmanifest", "/sw.js")


@app.before_request
def customer_guard():
    """🛡️ Customers only reach their own pages. Every driver/owner page and API is closed to them."""
    if session.get("role") != "customer":
        return None
    if request.path.startswith(CUSTOMER_ALLOWED):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "not available for customer accounts"}), 403
    return redirect(url_for("my_page"))


def orders_for_request() -> list:
    """All orders — except for a demo viewer, who only sees the demo ones."""
    orders = store.all()
    if session.get("role") == "viewer":
        return [o for o in orders if (o.note or "").startswith(DEMO_TAG)]
    return orders


@app.before_request
def check_panic_mode():
    """🔴 Emergency modes, checked before anything else on every request."""
    # 👀 viewers play in the sandbox: demo orders only, never real data or accounts
    if session.get("role") == "viewer":
        if request.path in ("/login", "/signup"):
            session.clear()                     # leaving the demo to sign in for real
            return None
        if request.method != "GET" and request.path != "/logout" and not _viewer_may_write():
            return jsonify({"ok": False, "error": "not available in the demo"}), 403
        if request.path.startswith(VIEWER_BLOCKED):
            return jsonify({"ok": False, "error": "not available in demo"}), 403
    mode = panic_mode()
    if mode == "normal":
        return None
    # The panic link itself must always work, or you could lock yourself out.
    if request.path.startswith("/panic/"):
        return None
    # Static files stay available so the pages that do load still render.
    if request.path.startswith("/static/"):
        return None

    if mode == "off":
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "service unavailable"}), 503
        return "<h1>Service temporarily unavailable</h1>", 503

    if mode == "lockdown":
        # Customers can still track their delivery; nobody can log in or stay in.
        if request.path.startswith("/track/") or request.path.startswith("/api/track/"):
            return None
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "locked down"}), 503
        return "<h1>Locked down</h1>", 503

    if mode == "readonly" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Logging out must keep working even when writes are frozen.
        if request.path == "/logout":
            return None
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "read-only mode"}), 503
        return "<h1>Read-only mode</h1>", 503

    return None


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


@app.route("/")
@owner_required
def home():
    return render_template("dashboard.html", is_owner=current_role() == "owner")


@app.route("/api/route")
@login_required
def api_route():
    mine = my_orders()
    me = driver_for(session.get("uid"))
    route = optimize_route(me, mine, geo)
    summary = route_summary(route)
    late_ids = set(summary.get("late_orders", []))
    delivered = sum(1 for o in mine if o.status == OrderStatus.DELIVERED)
    # Badge: orders assigned to me that arrived after I last looked.
    new_count = 0
    uid = session.get("uid")
    if uid and current_role() != "owner":
        seen = users.get_seen_at(uid)
        new_count = sum(1 for o in mine
                        if o.driver_id == uid
                        and (o.created_at or 0) > seen
                        and o.status == OrderStatus.PENDING)
    data = {
        "driver": {"lat": me.lat, "lng": me.lng, "name": me.name},
        "summary": {
            "stops": summary["stops"],
            "total_km": summary["total_km"],
            "finish": fmt(summary.get("finish_epoch")),
            "late_count": len(late_ids),
            "delivered": delivered,
            "new_count": new_count,
            "requested_count": sum(1 for o in orders_for_request() if o.status == OrderStatus.REQUESTED)
                               if current_role() == "owner" else 0,
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
                "eta_epoch": o.eta_epoch,
                "promised_epoch": o.promised_by,
                "price": o.price,                     # yuan to collect (None = not set)
                "paid": o.paid_at is not None,
                "pay_method": o.pay_method or "",
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

    # 4b) Price is optional: the order total in yuan.
    price = None
    price_raw = str(body.get("price", "")).strip()
    if price_raw:
        try:
            price = round(float(price_raw), 2)
        except ValueError:
            return bad_request("price must be a number")
        if not (0 <= price <= 100000):            # also rejects nan and inf
            return bad_request("price is out of range")

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

    # 6) Products are optional. When picked, the server prices them from the catalog plus the
    #    shop's delivery fee, and any price typed in the browser is ignored.
    raw_items = body.get("items")
    if raw_items not in (None, []):
        items, subtotal, error = catalog_items(raw_items)
        if error:
            return bad_request(error)
        fee = delivery_fee_for(subtotal, settings_store().all())
        price = round(subtotal + fee, 2)
        summary = ", ".join(f"{i['name']} x{i['qty']}" for i in items) + (f", delivery {fee:g}" if fee else "")
        note = (f"{note} | {summary}" if note else summary)[:200]
        kw["items"] = json.dumps(items, ensure_ascii=False)

    lat, lng, snapped = _snap_to_learned(phone, lat, lng)
    order = Order(customer_name=name, lat=lat, lng=lng, phone=phone, note=note, price=price, **kw)
    # Auto-assign to the nearest approved driver that has a GPS position.
    # If none is available, driver_id stays None and the order sits in the pool.
    nearest = find_nearest_driver(users, order.lat, order.lng)
    if nearest:
        order.driver_id = nearest["id"]
    _store_add(order)
    url = f"{request.host_url.rstrip('/')}/track/{order.id}?k={track_key(order.id)}"
    reply = f"好的，订单收到啦！点这里实时查看配送进度：{url}"
    return jsonify({"ok": True, "id": order.id, "track_url": url, "reply": reply, "snapped": snapped})


@app.route("/api/orders/<order_id>/deliver", methods=["POST"])
@login_required
def deliver_order(order_id):
    order = store.get(order_id)
    if order and visible_to_me(order):            # 🛡️ never someone else's order (IDOR)
        if order.status != OrderStatus.PENDING:
            return bad_request("order is not open")
        if order.driver_id is None and session.get("uid"):
            order.driver_id = session["uid"]      # a pool order belongs to whoever delivers it
        _mark_delivered(order)
        store.save()
        _learn_place(order)
        print(f"✅ Delivered: {order.customer_name}", flush=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

PAY_METHODS = ("cash", "wechat", "alipay")


@app.route("/api/orders/<order_id>/paid", methods=["POST"])
@login_required
def mark_paid(order_id):
    """Record that the customer paid (cash / wechat / alipay), or undo it with "none"."""
    order = store.get(order_id)
    if not order or not visible_to_me(order):      # never someone else's order (IDOR)
        return jsonify({"ok": False}), 404
    method = str((request.get_json(silent=True) or {}).get("method", "")).strip().lower()
    if method == "none":
        order.pay_method, order.paid_at = "", None
    elif method in PAY_METHODS:
        order.pay_method, order.paid_at = method, time.time()
    else:
        return bad_request("method must be cash, wechat, alipay or none")
    store.save()
    return jsonify({"ok": True, "paid": order.paid_at is not None, "method": order.pay_method})


@app.route("/api/orders/<order_id>/proof", methods=["POST"])
@login_required
def upload_proof(order_id):
    """📷 The driver uploads a delivery photo. Stored outside static/, login-only."""
    order = store.get(order_id)
    if not order or not visible_to_me(order):     # 🛡️ never someone else's order (IDOR)
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.DELIVERED:
        return bad_request("photo is only for a delivered order")

    file = request.files.get("photo")
    if not file or not file.filename:
        return bad_request("no photo uploaded")

    ext = ALLOWED_PROOF_TYPES.get(file.mimetype)
    if not ext:
        return bad_request("photo must be JPEG, PNG or WebP")

    # Read with a hard size cap, so a huge file can't fill the disk.
    data = file.read(MAX_PROOF_BYTES + 1)
    if len(data) > MAX_PROOF_BYTES:
        return bad_request("photo is too large (max 5 MB)")

    filename = f"{order_id}{ext}"                 # order_id is a random uuid; never the user's name
    with open(os.path.join(PROOF_DIR, filename), "wb") as f:
        f.write(data)
    photo_store().put(filename, data, file.mimetype)
    order.proof = filename
    store.save()
    print(f"📷 Proof saved: {order.customer_name} ({len(data)} bytes)", flush=True)
    return jsonify({"ok": True})


@app.route("/api/orders/<order_id>/proof", methods=["GET"])
@login_required
def get_proof(order_id):
    """📷 Serve a delivery photo — login-only, and never someone else's order."""
    order = store.get(order_id)
    if not order or not visible_to_me(order):     # 🛡️ IDOR protection
        return jsonify({"ok": False}), 404
    if not order.proof:
        return jsonify({"ok": False, "error": "no photo"}), 404
    # send_from_directory is safe: it refuses paths that escape PROOF_DIR.
    return proof_response(order.proof)


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
  if "price" in body:                         # empty value clears the price
    raw = str(body.get("price") or "").strip()
    if raw:
      try:
        new_price = round(float(raw), 2)
      except ValueError:
        return bad_request("price must be a number")
      if not (0 <= new_price <= 100000):
        return bad_request("price is out of range")
      order.price = new_price
    else:
      order.price = None
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

    # 💬 An owner question about the numbers ("今天收入多少?") is answered from the stats
    question = owner_ai.answer(text, stats_data, late_now)
    if question:
        return jsonify(question)

    # 🛠️ An owner command ("李明 已付款 微信") → preview card; nothing changes before Confirm.
    # Deliver/cancel found by phone is a pasted WeChat message: the intent card below handles it.
    if current_role() == "owner":
        preview = owner_preview(text)
        if preview and not (preview.get("error") or
                            (preview["kind"] in ("deliver", "cancel") and preview["by"] == "phone")):
            return jsonify(preview)

    orders = [{"id": o.id, "name": o.customer_name, "phone": o.phone,
               "status": o.status.value} for o in orders_for_request()]
    result = handle_message(text, orders)
    result["intent"], result["confidence"] = predict_intent(text)

    if result.get("order_id"):
        order = store.get(result["order_id"])
        result["name"] = order.customer_name
        if result["action"] == "reply":
            route = optimize_route(driver, orders_for_request(), geo)
            me = next((r for r in route if r.id == order.id), None)
            if me:
                result["reply"] = f"您好{order.customer_name}，您的订单预计 {fmt(me.eta_epoch)}  送达"

    # 🎨 What the AI understood (for highlighting) and how many orders the message holds
    from order_parser import parse_message as _parse, parse_many as _many
    result["entities"] = _parse(text)["entities"]
    result["many"] = len(_many(text))

    # 🧠 A regular customer? Remember who they are, where "老地方" is, and how much they usually take.
    from order_parser import find_phone, find_address
    import customers
    phone = find_phone(text)
    known = customers.profile(phone, orders_for_request())
    if known and (result.get("intent") == "new_order" or customers.wants_usual_place(text)):
        last = known["last_order"]
        result["regular"] = {
            "name": known["name"], "count": known["count"], "phone": phone,
            "note": public_note(last), "lat": last.lat, "lng": last.lng,
            "usual_place": customers.wants_usual_place(text) or not find_address(text),
            "qty": customers.quantity(text) or known["usual_qty"],
        }
    return jsonify(result)
_pending = None


def pending_actions() -> owner_ai.PendingActions:
    """Previewed owner actions, in the same database as the orders (follows store.path for tests)."""
    global _pending
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _pending is None or _pending.path != path:
        _pending = owner_ai.PendingActions(path)
    return _pending


def owner_preview(text: str) -> dict | None:
    """Parse an owner command and store it for Confirm. Returns the preview card data,
    {"kind", "error"} when the command is incomplete, or None when it is not a command."""
    open_drivers = [u for u in users.all() if u["status"] == "approved"]
    a = owner_ai.parse_action(text, [o for o in orders_for_request()
                                     if o.status != OrderStatus.CANCELLED], open_drivers)
    if not a or a.get("error"):
        return a
    order = store.get(a["order_id"]) if a.get("order_id") else None
    names = {}
    if order:
        names["order"] = order.customer_name + (f" ({order.phone})" if order.phone else "")
        names["old"] = f"{order.price:g}" if order.price is not None else "-"
    if a["kind"] == "assign":
        names["driver"] = (users.get(a["driver_id"]) or {}).get("name", "?")
    if a["kind"] == "fee":
        names["old"] = f"{settings_store().all()['delivery_fee']:g}"
    summary = owner_ai.describe(a, **names)
    return {"action": "preview", "kind": a["kind"], "lang": a["lang"], "by": a.get("by"),
            "summary": summary, "hint": owner_ai.CONFIRM_HINT[a["lang"]],
            "token": pending_actions().put({**a, "summary": summary})}


def run_owner_action(a: dict) -> tuple[bool, str]:
    """Carry out a confirmed action. Everything is checked again: the order may have
    changed between the preview and the Confirm press."""
    kind = a.get("kind")
    if kind == "add_product":
        fields, error = clean_product(a.get("name"), a.get("price"), a.get("unit", ""))
        if error:
            return False, error
        product_store().create(fields["name"], fields["price"], fields["unit"])
    elif kind == "fee":
        values, error = clean_settings({"delivery_fee": a.get("fee")})
        if error:
            return False, error
        settings_store().update(**values)
    else:
        order = store.get(str(a.get("order_id", "")))
        if not order:
            return False, "order not found"
        if kind == "assign":
            u = users.get(str(a.get("driver_id", "")))
            if not u or u["status"] != "approved":
                return False, "unknown or not approved driver"
            order.driver_id = u["id"]
        elif kind == "deliver":
            if order.status != OrderStatus.PENDING:
                return False, "order is not open"
            _mark_delivered(order)
        elif kind == "paid":
            if a.get("method") not in PAY_METHODS:
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
        store.save()
        if kind == "deliver":
            _learn_place(order)
    return True, owner_ai.DONE.get(a.get("lang"), owner_ai.DONE["en"]) + a.get("summary", "")


def confirm_owner_action(token: str, who: str) -> tuple[bool, str]:
    """Confirm button (web or Telegram): run the stored action once, and log it."""
    a = pending_actions().take(token)
    if not a:
        return False, "this preview expired or was already used"
    ok, message = run_owner_action(a)
    if ok:
        audit.add("ai_action", client_ip() if has_request_context() else "", who, a.get("summary", "")[:200])
    return ok, message


@app.route("/api/agent/confirm", methods=["POST"])
@owner_required
def agent_confirm():
    if current_role() != "owner":
        return jsonify({"ok": False, "error": "owner only"}), 403
    ok, message = confirm_owner_action((request.get_json(silent=True) or {}).get("token", ""), "owner")
    return (jsonify({"ok": True, "reply": message}) if ok
            else bad_request(message))


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


# Train on start; even when skipping is asked (tests), a fresh clone has no model file yet
if os.environ.get("SKIP_AUTO_RETRAIN") != "1" or not os.path.exists(MODEL_PATH):
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
    c_lat, c_lng = _demo_center()
    for name, phone, note, promise, north_km, east_km in DEMO_ORDERS:
        lat = c_lat + north_km / 111.0
        lng = c_lng + east_km / (111.0 * math.cos(math.radians(c_lat)))
        kw = {"promised_by": time.time() + promise * 60} if promise else {}
        order = Order(customer_name=name, lat=round(lat, 5), lng=round(lng, 5),
                      phone=phone, note=DEMO_TAG + note, **kw)
        _store_add(order)
        ids.append(order.id)
    print(f"✨ Demo: added {len(ids)} orders", flush=True)
    if not any((o.note or "").startswith(DEMO_TAG) and (o.created_at or 0) < time.time() - 2 * 86400
               for o in store.all()):
        seed_demo_history()  # owner pressed Demo: show the history too
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
    return jsonify(stats_data())


def stats_data() -> dict:
    """The stats numbers, shared by /api/stats and the owner AI so both always agree."""
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    per_day = {d: 0 for d in days}
    minutes = []
    by_hour = [0] * 24
    on_time = late = 0
    revenue = {d: 0.0 for d in days}               # yuan earned per day (delivered orders)
    to_collect = 0.0                              # delivered but not paid yet
    per_driver = {}                               # driver id → their numbers (same rules as above)
    products_sold = {}                            # product → qty, orders, revenue (delivered, last 7 days)

    for o in orders_for_request():
        if o.status != OrderStatus.DELIVERED or not o.delivered_at:
            continue
        done = datetime.fromtimestamp(o.delivered_at)
        if o.price:
            if done.date() in revenue:
                revenue[done.date()] += o.price
            if o.paid_at is None:
                to_collect += o.price
        p = per_driver.setdefault(o.driver_id, {"today": 0, "week": 0, "minutes": [], "on_time": 0, "late": 0})
        p["today"] += done.date() == today
        p["week"] += done.date() in per_day
        if o.created_at:
            p["minutes"].append((o.delivered_at - o.created_at) / 60)
        if o.promised_by:
            p["on_time" if o.delivered_at <= o.promised_by else "late"] += 1
        if done.date() in per_day:
            per_day[done.date()] += 1
            _count_items(o, products_sold)
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
    return {
        "drivers": drivers,
        "days": [d.strftime("%m-%d") for d in days],
        "delivered": [per_day[d] for d in days],
        "by_hour": by_hour,
        "total_delivered": sum(by_hour),
        "avg_minutes": round(statistics.median(minutes)) if minutes else None,
        "on_time_pct": round(on_time / promised * 100) if promised else None,
        "revenue": [round(revenue[d], 2) for d in days],
        "revenue_today": round(revenue[today], 2),
        "revenue_week": round(sum(revenue.values()), 2),
        "to_collect": round(to_collect, 2),
        "top_products": _top_products(products_sold),
    }


TOP_PRODUCTS = 8


def _count_items(order, sold: dict) -> None:
    """Add one delivered order's items to the running totals. Bad or old data is skipped."""
    try:
        items = json.loads(order.items) if order.items else []
    except (ValueError, TypeError):
        return
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        try:
            qty, price = int(it.get("qty", 0)), float(it.get("price", 0) or 0)
        except (ValueError, TypeError):
            continue
        if qty <= 0:
            continue
        key = str(it.get("id") or it["name"])          # same product, even if renamed later
        p = sold.setdefault(key, {"name": str(it["name"])[:40], "unit": str(it.get("unit", ""))[:10],
                                  "qty": 0, "orders": 0, "revenue": 0.0})
        p["qty"] += qty
        p["orders"] += 1
        p["revenue"] += qty * price


def _top_products(sold: dict) -> list:
    """Best sellers first (by quantity, then revenue), with each one's share of the total."""
    total = sum(p["qty"] for p in sold.values()) or 1
    top = sorted(sold.values(), key=lambda p: (-p["qty"], -p["revenue"]))[:TOP_PRODUCTS]
    return [{**p, "revenue": round(p["revenue"], 2), "share": round(p["qty"] / total * 100)} for p in top]


def _clean_gps_point(p, now):
    """(lat, lng, at) for one batch point, or None when it is unusable.
    ts is epoch milliseconds (what the phone's GPS gives); accuracy is optional, in metres."""
    if not isinstance(p, dict):
        return None
    try:
        lat, lng, at = float(p.get("lat")), float(p.get("lng")), float(p.get("ts")) / 1000
        acc = float(p["accuracy"]) if p.get("accuracy") is not None else 0.0
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= acc <= 10000):   # also rejects nan
        return None
    if not (now - GPS_MAX_AGE_S <= at <= now + GPS_CLOCK_SKEW_S):
        return None                              # too old, or in the future
    return lat, lng, min(at, now)                # a drifting clock never stamps "later than now"


GPS_MAX_POINTS = 100
GPS_MAX_AGE_S = 600          # points older than 10 minutes are dropped
GPS_CLOCK_SKEW_S = 30        # phone clocks drift a little: "the future" starts 30 s ahead


@app.route("/api/driver/location", methods=["POST"])
@login_required
def update_driver_location():
    """The driver's phone sends its GPS position; routes start from here.
    Two formats: the old single point {lat, lng}, or a batch {points: [{lat, lng, accuracy, ts}]}
    (also a bare list) from the offline queue. Bad batch points are dropped and counted, so one
    stale point never blocks the rest; the newest good point becomes the driver position."""
    body = request.get_json(silent=True)
    points = body if isinstance(body, list) else (body or {}).get("points") if isinstance(body, dict) else None
    rejected = 0
    if points is not None:
        if not isinstance(points, list) or not 1 <= len(points) <= GPS_MAX_POINTS:
            return bad_request(f"send between 1 and {GPS_MAX_POINTS} points")
        now = time.time()
        good = sorted((c for c in (_clean_gps_point(p, now) for p in points) if c), key=lambda c: c[2])
        rejected = len(points) - len(good)
        if not good:
            return bad_request("no valid points")
        lat, lng, at = good[-1]
    else:
        body = body if isinstance(body, dict) else {}
        try:
            lat = float(body.get("lat"))
            lng = float(body.get("lng"))
        except (TypeError, ValueError):
            return bad_request("lat and lng must be numbers")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return bad_request("lat/lng out of range")
        good, at = [(lat, lng, None)], None
    uid = session.get("uid")
    if uid:
        users.set_location(uid, lat, lng, at)    # 📍 this driver's own position (newest point)
        for p_lat, p_lng, p_at in good:          # every good point goes to the trail
            _remember_trail(uid, p_lat, p_lng, p_at)
    else:
        session["here"] = [lat, lng]             # this browser's start point (survives restarts)
        if current_role() == "owner":
            driver.lat, driver.lng = lat, lng    # default start for drivers without GPS
            _remember_trail("default", lat, lng)
        _move_demo_near(lat, lng)
    return jsonify({"ok": True, "accepted": len(good), "rejected": rejected})


@app.route("/api/driver/mark-seen", methods=["POST"])
@login_required
def mark_orders_seen():
    """The driver has seen their orders — clear the 'new' badge."""
    uid = session.get("uid")
    if uid:
        users.mark_seen(uid)
    return jsonify({"ok": True})
    return jsonify({"ok": True, "lat": lat, "lng": lng})


from products import ProductStore, clean as clean_product

_products = None


def product_store() -> ProductStore:
    """Product table in the same database as the orders (follows store.path, so tests stay isolated)."""
    global _products
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _products is None or _products.path != path:
        _products = ProductStore(path)
    return _products


from settings import SettingsStore, clean as clean_settings, delivery_fee_for

_settings = None


def settings_store() -> SettingsStore:
    """Shop settings in the same database as the orders (follows store.path for tests)."""
    global _settings
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _settings is None or _settings.path != path:
        _settings = SettingsStore(path)
    return _settings


@app.route("/api/settings/shop")
@owner_required
def get_shop_settings():
    return jsonify(settings_store().all())


@app.route("/api/settings/shop", methods=["PUT"])
@owner_required
def update_shop_settings():
    values, error = clean_settings(request.get_json(silent=True) or {})
    if error:
        return bad_request(error)
    return jsonify({"ok": True, "settings": settings_store().update(**values)})


@app.route("/api/me/shop")
@login_required
def shop_info():
    """What customers need to know before ordering: delivery fee and minimum order."""
    return jsonify(settings_store().all())


@app.route("/products")
@owner_required
def products_page():
    return render_template("products.html")


@app.route("/api/products")
@login_required
def list_products():
    """The catalog. The owner also sees hidden products (?all=1)."""
    show_hidden = current_role() == "owner" and request.args.get("all") == "1"
    return jsonify(product_store().all(include_hidden=show_hidden))


@app.route("/api/products", methods=["POST"])
@owner_required
def add_product():
    body = request.get_json(silent=True) or {}
    fields, error = clean_product(body.get("name", ""), body.get("price", ""), body.get("unit", ""))
    if error:
        return bad_request(error)
    product = product_store().create(fields["name"], fields["price"], fields["unit"])
    return jsonify({"ok": True, "product": product})


@app.route("/api/products/<product_id>", methods=["PUT"])
@owner_required
def edit_product(product_id):
    if not product_store().get(product_id):
        return jsonify({"ok": False}), 404
    body = request.get_json(silent=True) or {}
    fields, error = clean_product(body.get("name"), body.get("price"), body.get("unit"))
    if error:
        return bad_request(error)
    if "active" in body:
        fields["active"] = bool(body["active"])
    return jsonify({"ok": True, "product": product_store().update(product_id, **fields)})


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
    for o in sorted(orders_for_request(), key=lambda x: x.created_at or 0, reverse=True):
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
            "has_proof": bool(o.proof),       # 📷 is there a delivery photo?
            "rating": o.rating,               # ⭐ customer rating, if any
            "price": o.price,                 # order total in yuan (None = not set)
            "pay_method": o.pay_method or "",
            "paid": o.paid_at is not None,
        })
    return jsonify(items)


def public_note(order) -> str:
    """The order note as the customer should see it (internal demo tag removed)."""
    note = order.note or ""
    return note[len(DEMO_TAG):].strip() if note.startswith(DEMO_TAG) else note.strip()


@app.route("/api/track/<order_id>")
def api_track(order_id):
    """Public info for ONE order — safe to share with the customer."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "error": "order not found"}), 404
    resp = track_payload(order_id, order)
    resp["shop_phone"] = os.environ.get("SHOP_PHONE", "")
    return jsonify(resp)


ARRIVED_KM = 0.08          # driver this close to the next stop = arrived
ARRIVED_FRESH_S = 120      # older GPS than this proves nothing


def driver_has_arrived(order, routed) -> bool:
    """True when this order is the driver's next stop and their fresh GPS is right there."""
    from geo import haversine_km
    if not routed or routed.sequence != 1 or not order.driver_id:
        return False
    u = users.get(order.driver_id) or {}
    if u.get("lat") is None or time.time() - (u.get("located_at") or 0) > ARRIVED_FRESH_S:
        return False
    return haversine_km(u["lat"], u["lng"], order.lat, order.lng) <= ARRIVED_KM


def track_payload(order_id, order) -> dict:
    """What the tracking page shows for one order (without the shop contact)."""
    if order.status == OrderStatus.DELIVERED:
        return ({"ok": True, "status": "delivered", "name": order.customer_name, "rating": order.rating,
                        "has_proof": bool(order.proof), "note": public_note(order),
                        "created_at": order.created_at, "delivered_at": getattr(order, "delivered_at", None)})
    if order.status == OrderStatus.CANCELLED:
        return ({"ok": True, "status": "cancelled", "name": order.customer_name})
    if order.status == OrderStatus.REQUESTED:
        return ({"ok": True, "status": "requested", "name": order.customer_name,
                 "note": public_note(order), "created_at": order.created_at, "price": order.price})

    # Where is this order in the current route?
    same_driver = [o for o in orders_for_request() if o.driver_id == order.driver_id]
    route = optimize_route(driver_for(order.driver_id), same_driver, geo)
    me = next((o for o in route if o.id == order_id), None)
    return ({
        "ok": True,
        "status": "on_the_way",
        "arrived": driver_has_arrived(order, me),
        "name": order.customer_name,
        "position": me.sequence if me else None,
        "stops_before": (me.sequence - 1) if me else None,
        "eta": fmt(me.eta_epoch) if me else "--",
        "promise": fmt(order.promised_by) if order.promised_by else None,
        "eta_epoch": me.eta_epoch if me else None,     # raw seconds, for the live countdown
        "promised_epoch": order.promised_by,
        "created_at": order.created_at,
        "note": public_note(order),
        "lat": order.lat,
        "lng": order.lng,
    })


@app.route("/api/track/<order_id>/rate", methods=["POST"])
def api_rate(order_id):
    """The customer rates their delivery (1-5). Public, but needs the signed key."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "error": "order not found"}), 404
    if order.status != OrderStatus.DELIVERED:
        return bad_request("you can only rate a delivered order")
    if order.rating is not None:
        return bad_request("this order is already rated")
    body = request.get_json(silent=True) or {}
    try:
        stars = int(body.get("rating"))
    except (TypeError, ValueError):
        return bad_request("rating must be a number")
    if not 1 <= stars <= 5:
        return bad_request("rating must be between 1 and 5")
    order.rating = stars
    store.save()
    print(f"⭐ Rated: {order.customer_name} → {stars}/5", flush=True)
    return jsonify({"ok": True, "rating": stars})


@app.route("/api/track/<order_id>/proof")
def track_proof(order_id):
    """📷 Serve the delivery photo to the customer — needs the signed key, no login."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = store.get(order_id)
    if not order or not order.proof:
        return jsonify({"ok": False, "error": "no photo"}), 404
    # send_from_directory is safe: it refuses paths that escape PROOF_DIR.
    return proof_response(order.proof)


DEMO_MAX_AGE = 24 * 3600          # demo orders are rebuilt once a day


def seed_demo_orders(center=None) -> list:
    """Add the demo scenario around `center` (default: this browser's GPS or ZAFU). Returns the new ids."""
    import math
    ids = []
    c_lat, c_lng = center or _demo_center()
    for name, phone, note, promise, north_km, east_km in DEMO_ORDERS:
        lat = c_lat + north_km / 111.0
        lng = c_lng + east_km / (111.0 * math.cos(math.radians(c_lat)))
        kw = {"promised_by": time.time() + promise * 60} if promise else {}
        order = Order(customer_name=name, lat=round(lat, 5), lng=round(lng, 5),
                      phone=phone, note=DEMO_TAG + note, **kw)
        store.add(order)
        ids.append(order.id)
    return ids


# Two regulars who stopped ordering (name, phone, every N days, place, km north, km east, silent days)
DEMO_QUIET = [("李明", "13700137011", 7, "阳光小区 3单元502室", -0.6, 0.9, 21),
              ("周婷", "13500135007", 5, "花园小区 2栋1201", 0.9, -1.1, 18)]


def seed_demo_history(days: int = 42) -> int:
    """📈 Six weeks of realistic past deliveries for the demo, so stats and the AI have
    something to show: regulars every few days, lunch and evening rush hours, ratings,
    and two regulars who went quiet. All tagged demo: never mixed with real orders."""
    import math, random
    rnd = random.Random(42)                       # same history every time: predictable demo
    now = time.time()
    people = [(name, phone, 4 + i % 5, note, north, east, 0)
              for i, (name, phone, note, _promise, north, east) in enumerate(DEMO_ORDERS)] + DEMO_QUIET
    added = 0
    for name, phone, every, place, north, east, silent in people:
        lat = round(driver.lat + north / 111.0, 5)
        lng = round(driver.lng + east / (111.0 * math.cos(math.radians(driver.lat))), 5)
        qty = rnd.choice([1, 2, 2, 3])
        t = now - days * 86400 + rnd.uniform(0, every) * 86400
        while t < now - max(silent, 1) * 86400:
            created = datetime.fromtimestamp(t).replace(
                hour=rnd.choice([10, 11, 11, 12, 16, 17, 17, 18]), minute=rnd.randrange(60)).timestamp()
            if created < now - 3600:
                took = rnd.randint(18, 45)
                store.add(Order(customer_name=name, lat=lat, lng=lng, phone=phone,
                                note=f"{DEMO_TAG}{qty}桶 {place}", created_at=created,
                                promised_by=created + 45 * 60 if rnd.random() < .6 else None,
                                status=OrderStatus.DELIVERED, delivered_at=created + took * 60,
                                rating=rnd.choice([5, 5, 5, 4, 4, None])))
                added += 1
            t += (every + rnd.uniform(-1, 1)) * 86400
    return added


def ensure_demo_data() -> None:
    """Visitors always land on a full, fresh demo: today's orders plus six weeks of history.
    Today's orders are rebuilt daily or when a promise is long past. Real orders are never touched."""
    now = time.time()
    demo = [o for o in store.all() if (o.note or "").startswith(DEMO_TAG)]
    live = [o for o in demo if o.status == OrderStatus.PENDING]
    late = any(o.promised_by and o.promised_by < now - 1800 for o in live)
    stale = bool(live) and min(o.created_at or 0 for o in live) < now - DEMO_MAX_AGE
    if late or stale:
        store.orders = [o for o in store.orders
                        if not ((o.note or "").startswith(DEMO_TAG) and o.status == OrderStatus.PENDING)]
        store.save()
        live = []
    if not live:
        seed_demo_orders()
    if not any((o.created_at or now) < now - 2 * 86400 for o in demo):
        seed_demo_history()


@app.route("/try")
def try_demo():
    """👀 Public demo entrance from the login page. A sandbox: visitors only ever touch demo data."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = session_version()
    audit.add("demo_open", client_ip(), "viewer", "public")
    ensure_demo_data()
    return redirect(url_for("home"))


@app.route("/demo")
def demo_login():
    """👀 Signed demo link: opens the dashboard in the demo sandbox, no password needed."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, demo_key().encode()):
        return jsonify({"ok": False, "error": "not found"}), 404
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = session_version()          # "Logout everyone" ends demos too
    audit.add("demo_open", client_ip(), "viewer")
    ensure_demo_data()
    return redirect(url_for("home"))


@app.route("/track/<order_id>")
def track_page(order_id):
    return render_template("track.html", order_id=order_id, shop_name=os.environ.get("SHOP_NAME", "智送管家"))


@app.route("/api/driver/today")
@login_required
def driver_today():
    """The driver's own day: deliveries done and money collected (never another driver's)."""
    today = datetime.now().date()
    uid = session.get("uid")
    done = [o for o in my_orders()
            if o.status == OrderStatus.DELIVERED and o.delivered_at
            and datetime.fromtimestamp(o.delivered_at).date() == today
            and (not uid or o.driver_id == uid)]
    paid = lambda kinds: round(sum(o.price or 0 for o in done if o.paid_at and o.pay_method in kinds), 2)
    return jsonify({"ok": True, "delivered": len(done), "cash": paid(("cash",)),
                    "online": paid(("wechat", "alipay")),
                    "to_collect": round(sum(o.price or 0 for o in done if o.price and not o.paid_at), 2)})


@app.route("/driver")
@login_required
def driver_page():
    return render_template("driver.html", role=current_role())


# ---------- Learned ETA (AI): retrained from finished deliveries ----------
import optimizer as _optimizer_mod  # noqa: E402
from eta_model import EtaModel, legs_from_orders  # noqa: E402

ETA_RETRAIN_SECONDS = 30 * 60
_eta_cache = {"model": None, "at": 0.0, "store": None, "info": None}


def learned_eta_model():
    """Learned ETA model, retrained at most every 30 minutes. None = simple formula."""
    if time.time() - _eta_cache["at"] > ETA_RETRAIN_SECONDS or _eta_cache["store"] is not store:
        _eta_cache.update(at=time.time(), store=store)
        try:
            legs = legs_from_orders(
                store.all(),
                baseline=lambda a, b: geo.distance_time(a.lat, a.lng, b.lat, b.lng)[1] + b.service_minutes)
            model = EtaModel.train(legs)
            _eta_cache["model"] = model if (model and model.active) else None
            _eta_cache["info"] = model.info() if model else {"active": False, "legs": len(legs)}
        except Exception as e:                       # never break routing because of the model
            print(f"ETA model training failed: {e}", flush=True)
            _eta_cache["model"] = None
    return _eta_cache["model"]


_optimizer_mod.ETA_PROVIDER = learned_eta_model


@app.route("/api/eta-model")
@owner_required
def eta_model_info():
    """What the learned ETA model knows (Stats page)."""
    learned_eta_model()
    return jsonify(_eta_cache["info"] or {"active": False, "legs": 0})


# ---------- Plan B for GPS: the visitor's city from their IP ----------
_IP_GEO = OrderedDict()          # ip -> (lat, lng, city) or None, newest last


def _ip_location(ip):
    """Approximate (lat, lng, city) in WGS-84 for a public IP, or None. Cached, 3 s timeout."""
    import ipaddress
    try:
        if not ipaddress.ip_address(ip).is_global:
            return None
    except ValueError:
        return None
    if ip in _IP_GEO:
        return _IP_GEO[ip]
    try:
        r = requests.get(f"https://ipapi.co/{ip}/json/", timeout=3,
                         headers={"User-Agent": "zhisong-delivery/1.0"})
        d = r.json()
        loc = (float(d["latitude"]), float(d["longitude"]),
               str(d.get("city") or d.get("country_name") or "")[:60])
    except Exception:
        loc = None                                # offline, rate-limited, unknown IP
    _IP_GEO[ip] = loc
    if len(_IP_GEO) > 500:
        _IP_GEO.popitem(last=False)
    return loc


@app.route("/api/where-am-i")
@login_required
def where_am_i():
    """Demo visitors without GPS land on their own city. Never moves the owner's real data."""
    if current_role() != "viewer" or session.get("here"):
        return jsonify({"ok": False})
    loc = _ip_location(client_ip())
    if not loc:
        return jsonify({"ok": False})
    lat, lng = wgs84_to_gcj02(loc[0], loc[1])     # same coordinates as the Chinese map
    session["here"] = [lat, lng]
    _move_demo_near(lat, lng)
    return jsonify({"ok": True, "lat": lat, "lng": lng, "city": loc[2]})


# ---------- Learned delivery spots: where each customer really receives orders ----------
from places import PlaceStore  # noqa: E402

LEARN_MAX_KM = 0.3     # driver GPS further than this from the pin: probably not at the door
SNAP_MAX_KM = 0.5      # new pin further than this from the learned spot: a different address
_places = None


def place_store() -> PlaceStore:
    """Learned spots in the same database as the orders (follows store.path for tests)."""
    global _places
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _places is None or _places.path != path:
        _places = PlaceStore(path)
    return _places


def _learn_place(order) -> None:
    """The driver just delivered: remember where they really were, for this customer's phone."""
    from geo import haversine_km
    if not order.phone or (order.note or "").startswith(DEMO_TAG) or not session.get("uid"):
        return
    u = users.get(session["uid"]) or {}
    if u.get("lat") is None or time.time() - (u.get("located_at") or 0) > ARRIVED_FRESH_S:
        return                                   # no fresh GPS: nothing reliable to learn
    if haversine_km(u["lat"], u["lng"], order.lat, order.lng) > LEARN_MAX_KM:
        return
    place_store().learn(order.phone, u["lat"], u["lng"])


def _snap_to_learned(phone, lat, lng):
    """Move a sloppy pin onto the spot drivers really delivered to. Returns (lat, lng, snapped)."""
    from geo import haversine_km
    spot = place_store().get(phone) if phone else None
    if spot and haversine_km(spot[0], spot[1], lat, lng) <= SNAP_MAX_KM:
        return round(spot[0], 6), round(spot[1], 6), True
    return lat, lng, False


# ---------- GPS watch: Telegram alerts for lost GPS, stopped drivers, late orders ----------
WATCH_STALE_S = 300        # open orders but no GPS for 5 min
WATCH_STILL_S = 600        # not moved for 10 min
WATCH_STILL_M = 50         # "not moved" = stayed within 50 m
WATCH_DOOR_KM = 0.15       # standing this close to a customer = handing over, not stuck
_watch = {"still": {}, "sent": set()}   # driver id -> (lat, lng, since); alerts already sent


def gps_watch(now=None) -> list:
    """One check of every working driver. Returns new alert texts (each sent once per problem)."""
    from geo import haversine_km
    now = now or time.time()
    sent, out = _watch["sent"], []
    open_orders = [o for o in store.all()
                   if o.status == OrderStatus.PENDING and not (o.note or "").startswith(DEMO_TAG)]
    for u in users.all():
        if u.get("role", "driver") != "driver" or u.get("status") != "approved":
            continue
        uid, name = u["id"], u["name"]
        mine = [o for o in open_orders if o.driver_id == uid]
        if not mine or u.get("lat") is None:            # not working: forget old state
            _watch["still"].pop(uid, None)
            sent.discard(f"stale:{uid}")
            sent.discard(f"still:{uid}")
            continue

        # 1) GPS lost (but not "never had GPS today")
        age = now - (u.get("located_at") or 0)
        if WATCH_STALE_S < age < 7200:
            if f"stale:{uid}" not in sent:
                sent.add(f"stale:{uid}")
                out.append(f"GPS lost: {name} sent no position for {int(age // 60)} min "
                           f"({len(mine)} orders open)")
        else:
            sent.discard(f"stale:{uid}")

        # 2) Stopped for a long time, away from any customer
        if age <= WATCH_STALE_S:
            here = (u["lat"], u["lng"])
            prev = _watch["still"].get(uid)
            if not prev or haversine_km(prev[0], prev[1], here[0], here[1]) * 1000 > WATCH_STILL_M:
                _watch["still"][uid] = (here[0], here[1], now)
                sent.discard(f"still:{uid}")
            else:
                at_door = any(haversine_km(o.lat, o.lng, here[0], here[1]) <= WATCH_DOOR_KM for o in mine)
                if now - prev[2] > WATCH_STILL_S and not at_door and f"still:{uid}" not in sent:
                    sent.add(f"still:{uid}")
                    out.append(f"Driver stopped: {name} has not moved for {int((now - prev[2]) // 60)} min "
                               f"({len(mine)} orders open)")

    # 3) An open order that will arrive after its promise: one alert per order, ever
    for late in late_now(open_orders, now):
        if f"late:{late['id']}" not in sent:
            sent.add(f"late:{late['id']}")
            out.append(f"Running late: {late['customer']} ({late['driver'] or 'no driver'}) "
                       f"~{late['minutes_late']} min late, expected {fmt(late['eta'])}, "
                       f"promised {fmt(late['promised'])}, order {late['id']}")
    return out


def late_now(orders=None, now=None) -> list:
    """Open orders whose predicted ETA is after their promise (or the promise already passed),
    worst first. Same routing as each driver's screen: a driver's open orders from their GPS
    position (or the default start), unassigned orders from the default start."""
    import math
    now = now or time.time()
    by_driver = {}
    for o in (orders_for_request() if orders is None else orders):
        if o.is_open:
            by_driver.setdefault(o.driver_id, []).append(o)
    out = []
    for uid, mine in by_driver.items():
        user = users.get(uid) if uid else None
        # Orders without a promise still ride along: they push the others' ETAs back.
        for o in optimize_route(driver_for(uid), mine, geo, start_time=now):
            if o.sequence != 1:
                _NOT_NEXT_AT[o.id] = now         # trail safety: this order is not being driven to
            if o.promised_by and o.eta_epoch and o.eta_epoch > o.promised_by:
                out.append({"id": o.id, "customer": o.customer_name,
                            "driver": user["name"] if user else None,
                            "minutes_late": math.ceil((o.eta_epoch - o.promised_by) / 60),
                            "eta": o.eta_epoch, "promised": o.promised_by})
    return sorted(out, key=lambda x: -x["minutes_late"])


def _gps_watch_loop():
    """Every minute, in the background: check drivers and send new alerts to Telegram."""
    import alerts
    while True:
        time.sleep(60)
        try:
            for msg in gps_watch():
                alerts._telegram(msg)
        except Exception as e:                    # never let the watch crash the app
            print(f"GPS watch failed: {e}", flush=True)


import sys as _sys  # noqa: E402
if os.environ.get("GPS_WATCH", "1") == "1" and "pytest" not in _sys.modules:
    import threading as _threading  # noqa: E402
    _threading.Thread(target=_gps_watch_loop, daemon=True, name="gps-watch").start()


# ---------- Separate demos: the driver's side and the customer's side ----------
def _open_demo_session() -> None:
    """A fresh demo visitor (viewer): sandboxed, only ever touches demo orders."""
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = session_version()
    ensure_demo_data()


@app.route("/try/exit")
def try_exit():
    """Leave the demo and land on the real login page. Only demo sessions are cleared,
    so a link to this page can never log a real owner, driver or customer out."""
    if session.get("role") == "viewer" or session.get("demo"):
        session.clear()
    return redirect(url_for("login"))


@app.route("/try/driver")
def try_demo_driver():
    """Public demo of the driver's phone: next stop, Navigate, Delivered, live GPS."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    _open_demo_session()
    audit.add("demo_open", client_ip(), "viewer", "driver")
    return redirect("/driver")


DEMO_CUSTOMER_NAME = "体验用户 · demo"    # marks throwaway demo customer accounts
DEMO_CUSTOMER_TTL = 24 * 3600


def _cleanup_demo_customers() -> None:
    """Throwaway demo customer accounts live one day, then go (with their order links)."""
    con = users._connect()
    try:
        users._customer_orders_table(con)      # make sure it exists (a failed query aborts a PG transaction)
        old = [r[0] for r in con.execute("SELECT id FROM users WHERE name = ? AND created_at < ?",
                                         (DEMO_CUSTOMER_NAME, time.time() - DEMO_CUSTOMER_TTL)).fetchall()]
        for uid in old:
            con.execute("DELETE FROM customer_orders WHERE user_id = ?", (uid,))
            con.execute("DELETE FROM users WHERE id = ?", (uid,))
        con.commit()
    finally:
        con.close()


def _new_demo_customer() -> dict:
    """A fresh customer account for one demo visitor (nobody sees another visitor's orders)."""
    for _ in range(5):
        phone = "199" + "".join(secrets.choice("0123456789") for _ in range(8))
        if users.by_phone(phone):
            continue
        try:
            return users.create(phone, DEMO_CUSTOMER_NAME, "Demo!" + secrets.token_hex(10) + "Zz9",
                                role="customer")
        except ValueError:
            continue
    raise RuntimeError("could not create a demo customer")


@app.route("/try/customer")
def try_demo_customer():
    """Public demo of the customer's side: the full customer screen, with an order on its way."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    session.clear()
    ensure_demo_data()
    _cleanup_demo_customers()
    me = _new_demo_customer()
    # Their order: the demo driver's next stop, so the road to their door shows at once
    live = [o for o in store.all() if (o.note or "").startswith(DEMO_TAG)
            and o.status == OrderStatus.PENDING and o.driver_id is None]
    route = optimize_route(driver_for(None), live, geo) if live else []
    if route:
        users.link_order(me["id"], route[0].id)
    session["auth"] = True
    session["role"] = "customer"
    session["uid"] = me["id"]
    session["pv"] = me.get("pw_version", 0)
    session["v"] = session_version()
    session["demo"] = True
    audit.add("demo_open", client_ip(), "viewer", "customer")
    return redirect("/me")


# ---------- Customer live view: the driver coming, the road once they are next ----------
from collections import deque  # noqa: E402

TRAIL_KEEP_S = 1800        # trail = the last 30 minutes of driving at most
TRAIL_HIDE_KM = 0.25       # skip the start of the trail: it sits at the previous customer's door
TRAIL_MAX_POINTS = 2000    # per driver in memory: 30 min at one point per second
TRAIL_SAVE_MAX = 300       # points kept with a delivered order
# order id -> last time this order was seen NOT being its driver's next stop (customer polls and the
# minute GPS watch both record it). ponytail: in memory, like _TRAILS; a restart only shortens trails.
_NOT_NEXT_AT = {}
_TRAILS = {}               # driver id (or "default") -> deque of (time, lat, lng)
_ROAD_LINES = OrderedDict()


def _remember_trail(key, lat, lng, at=None) -> None:
    q = _TRAILS.setdefault(key, deque(maxlen=TRAIL_MAX_POINTS))
    at = at or time.time()
    if q and at < q[-1][0]:
        return                                   # older than the trail's end: would draw a zigzag
    if not q or abs(q[-1][1] - lat) > 1e-5 or abs(q[-1][2] - lng) > 1e-5:
        q.append((at, lat, lng))


def _previous_door(order, now):
    """(lat, lng) of the driver's previous delivery (the last 30 min), or None. Never shown:
    the driver marker and the trail stay hidden near it, so it never reveals that address."""
    done = [o for o in store.all() if o.driver_id == order.driver_id and o.id != order.id
            and o.status == OrderStatus.DELIVERED and (o.delivered_at or 0) >= now - TRAIL_KEEP_S]
    last = max(done, key=lambda o: o.delivered_at, default=None)
    return (last.lat, last.lng) if last else None


def _near(a, b) -> bool:
    from geo import haversine_km
    return bool(a and b) and haversine_km(a[0], a[1], b[0], b[1]) < TRAIL_HIDE_KM


def _trail_start(order) -> float:
    """When this order's own trail begins: the driver's previous delivery, the order's creation,
    or the last moment it was seen NOT being next, whichever is latest. So the trail never
    contains the drive toward another customer's door."""
    last_done = max((o.delivered_at or 0 for o in store.all()
                     if o.driver_id == order.driver_id and o.id != order.id
                     and o.status == OrderStatus.DELIVERED), default=0)
    return max(last_done, order.created_at or 0, _NOT_NEXT_AT.get(order.id, 0))


def _order_trail(order, until) -> list:
    """The assigned driver's path for THIS order only, without the previous customer's door."""
    from geo import haversine_km
    if not order.driver_id:
        return []                                # pool order: nobody is driving to it yet
    since = max(_trail_start(order), until - TRAIL_KEEP_S)
    door = _previous_door(order, until)
    pts = [(p[1], p[2]) for p in _TRAILS.get(order.driver_id, ())
           if since <= p[0] <= until and not _near(door, (p[1], p[2]))]
    if pts and not door:                         # previous door unknown: hide around where it starts
        start = pts[0]
        pts = [p for p in pts if haversine_km(start[0], start[1], p[0], p[1]) >= TRAIL_HIDE_KM]
    return [list(p) for p in pts]


def _simplify(pts: list, n: int = TRAIL_SAVE_MAX) -> list:
    """At most n points, first and last kept.
    ponytail: even stride, not Douglas-Peucker; switch if saved trails cut corners visibly."""
    if len(pts) <= n:
        return pts
    step = (len(pts) - 1) / (n - 1)
    return [pts[round(i * step)] for i in range(n)]


def _mark_delivered(order) -> None:
    """Every delivery goes through here: status, time, and this order's own trail saved with it."""
    now = time.time()
    trail = _simplify(_order_trail(order, now))
    order.status, order.delivered_at = OrderStatus.DELIVERED, now
    order.trail = json.dumps([[round(a, 6), round(b, 6)] for a, b in trail]) if trail else ""
    _NOT_NEXT_AT.pop(order.id, None)


def _road_line(a, b) -> list:
    """Road from a to b as [[lat, lng], ...] in map coordinates; a straight line if routing fails."""
    key = (round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4))
    if key in _ROAD_LINES:
        return _ROAD_LINES[key]
    path = [list(a), list(b)]
    try:
        wa, wb = gcj02_to_wgs84(a[0], a[1]), gcj02_to_wgs84(b[0], b[1])
        r = requests.get(f"https://router.project-osrm.org/route/v1/driving/{wa[1]},{wa[0]};{wb[1]},{wb[0]}",
                         params={"overview": "full", "geometries": "geojson"}, timeout=4)
        coords = r.json()["routes"][0]["geometry"]["coordinates"]
        path = [list(wgs84_to_gcj02(lat, lng)) for lng, lat in coords]
    except Exception:
        pass                                      # offline or routing down: keep the straight line
    _ROAD_LINES[key] = path
    if len(_ROAD_LINES) > 300:
        _ROAD_LINES.popitem(last=False)
    return path


@app.route("/api/me/live")
@login_required
def my_live():
    """The customer's open orders: where the driver is, and the road only once they are next."""
    if current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    now = time.time()
    items = []
    for oid in users.customer_order_ids(session["uid"]):
        o = store.get(oid)
        if not o or o.status not in (OrderStatus.PENDING, OrderStatus.REQUESTED):
            continue
        item = {"id": o.id, "status": o.status.value, "lat": o.lat, "lng": o.lng, "price": o.price,
                "note": public_note(o), "track_url": f"/track/{o.id}?k={track_key(o.id)}"}
        if o.status == OrderStatus.PENDING:
            same = [x for x in store.all() if x.driver_id == o.driver_id]
            me = next((x for x in optimize_route(driver_for(o.driver_id), same, geo, start_time=now)
                       if x.id == o.id), None)
            # 🛡️ The driver, their road and their trail only for an assigned order that is the
            # driver's next stop. Before that: how many stops, never where the driver is.
            # A demo visitor's unassigned demo order is driven by the demo driver (the shop's
            # default point, not a person), so the demo still shows a road at once.
            demo = o.driver_id is None and (o.note or "").startswith(DEMO_TAG)
            is_next = bool(me and me.sequence == 1 and (o.driver_id or demo))
            if not is_next:
                _NOT_NEXT_AT[o.id] = now
            u = users.get(o.driver_id) if is_next and o.driver_id else None
            drv = ([u["lat"], u["lng"]] if u and u.get("lat") is not None
                   else [driver.lat, driver.lng] if is_next and demo else None)
            if drv and o.driver_id and _near(_previous_door(o, now), drv):
                drv = None                       # still at the previous customer's door: not shown
            item.update({
                "eta": fmt(me.eta_epoch) if me else "--",
                "eta_epoch": me.eta_epoch if me else None,
                "stops_before": (me.sequence - 1) if me else None,
                "promise": fmt(o.promised_by) if o.promised_by else None,
                "arrived": driver_has_arrived(o, me),
                "next": is_next,
                "driver": drv,
                "driver_age_s": round(now - (u.get("located_at") or 0)) if u and drv else None,
                "road": _road_line(tuple(drv), (o.lat, o.lng)) if drv else [],
                "trail": _order_trail(o, now) if drv else [],
            })
        items.append(item)
    return jsonify({"ok": True, "orders": items})


# Route modules do `import fileapp as core`. When this file runs as a script (__main__),
# point that import at this module instead of loading a second copy of the app.
import sys as _sys_mod  # noqa: E402
_sys_mod.modules.setdefault("fileapp", _sys_mod.modules[__name__])

from routes import pwa  # noqa: E402,F401


from routes import telegram  # noqa: E402,F401


from routes import auth  # noqa: E402,F401


if __name__ == "__main__":
    app.run(debug=True, port=5000)