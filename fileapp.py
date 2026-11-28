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


import requests  # noqa: E402
from collections import OrderedDict  # noqa: E402
from geo import wgs84_to_gcj02, gcj02_to_wgs84  # noqa: E402


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


_pending = None


def pending_actions() -> owner_ai.PendingActions:
    """Previewed owner actions, in the same database as the orders (follows store.path for tests)."""
    global _pending
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _pending is None or _pending.path != path:
        _pending = owner_ai.PendingActions(path)
    return _pending


FEEDBACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl")


DEMO_TAG = "🎬 "


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


# ---------- Learned ETA (AI): retrained from finished deliveries ----------
import optimizer as _optimizer_mod  # noqa: E402
from eta_model import EtaModel, legs_from_orders  # noqa: E402


# ---------- Learned delivery spots: where each customer really receives orders ----------
from places import PlaceStore  # noqa: E402

_places = None


def place_store() -> PlaceStore:
    """Learned spots in the same database as the orders (follows store.path for tests)."""
    global _places
    path = getattr(store, "path", os.path.join(HERE, "orders.db"))
    if _places is None or _places.path != path:
        _places = PlaceStore(path)
    return _places


# ---------- Customer live view: the driver coming, the road once they are next ----------
from collections import deque  # noqa: E402


# Route modules do `import fileapp as core`. When this file runs as a script (__main__),
# point that import at this module instead of loading a second copy of the app.
import sys as _sys_mod  # noqa: E402
_sys_mod.modules.setdefault("fileapp", _sys_mod.modules[__name__])

import routes.pwa  # noqa: E402,F401


import routes.telegram  # noqa: E402,F401


import routes.auth  # noqa: E402,F401


import routes.stats  # noqa: E402,F401
from routes.stats import stats_data  # noqa: E402,F401


import routes.ai  # noqa: E402,F401
from routes.ai import confirm_owner_action, owner_preview, run_owner_action  # noqa: E402,F401


import routes.demo  # noqa: E402,F401
from routes.demo import DEMO_CUSTOMER_TTL, _cleanup_demo_customers, _ip_location, _move_demo_near  # noqa: E402,F401


import routes.driver  # noqa: E402,F401
from routes.driver import _watch, gps_watch, late_now  # noqa: E402,F401


import routes.orders  # noqa: E402,F401
from routes.orders import PAY_METHODS, _learn_place, _snap_to_learned  # noqa: E402,F401


import routes.customer  # noqa: E402,F401
from routes.customer import ARRIVED_FRESH_S, MAX_OPEN_REQUESTS, TRAIL_SAVE_MAX, _NOT_NEXT_AT, _TRAILS, _mark_delivered, _notify_new_request, _remember_trail, _road_line, _trail_start, catalog_items, public_note  # noqa: E402,F401


if __name__ == "__main__":
    app.run(debug=True, port=5000)