"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import hmac
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
        store.save()
        print(f"✅ Delivered: {order.customer_name}", flush=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


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
    input {
      width: 100%; padding: 11px; margin: 6px 0;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 8px;
      color: #e8eaed; font-size: 14px;
    }
    input:focus { outline: none; border-color: #2d6cdf; }
    input::placeholder { color: #5a5f68; }
    input.invalid { border-color: #e5484d; }
    button {
      width: 100%; padding: 12px; background: #2d6cdf; color: white;
      border: none; border-radius: 8px; cursor: pointer; margin-top: 10px;
      font-size: 14px; font-weight: 600; transition: background 0.15s;
    }
    button:hover { background: #245bc0; }
    .stop {
      padding: 12px; margin-bottom: 8px; font-size: 14px;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 10px;
      animation: slideIn 0.25s ease;
    }
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
      <a class="logout" href="/logout" title="Log out">Logout</a>
    </div>

    <div class="gps-row">
      <button class="gps-btn" onclick="locateOnce()">📍 Use my location</button>
      <button class="gps-btn" id="live-btn" onclick="toggleLive()">🛰️ Live GPS: off</button>
    </div>
    <div class="gps-status" id="gps-status"></div>

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
    const map = L.map('map').setView([31.2304, 121.4737], 13);
    L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
      subdomains: ['1', '2', '3', '4'], attribution: '&copy; AutoNavi'
    }).addTo(map);
    let layer = L.layerGroup().addTo(map);
    let pickMarker = null;

    async function api(url, options) {
      const res = await fetch(url, options);
      if (res.status === 401) { location.href = '/login'; throw new Error('login required'); }
      return res;
    }
    let lastStopCount = -1;

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
      await refresh();
      highlightNew(data.id);
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

    async function sendLocation(pos) {
      const lat = pos.coords.latitude, lng = pos.coords.longitude;
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

    async function refresh() {
      const res = await api('/api/route');
      const data = await res.json();
      layer.clearLayers();
      const pts = [[data.driver.lat, data.driver.lng]];
      L.circleMarker([data.driver.lat, data.driver.lng],
        {radius: 9, color: '#1f9d55', fillColor: '#1f9d55', fillOpacity: 1})
        .addTo(layer).bindPopup('🚚 Driver (start)');

      let html = '';
      data.stops.forEach(s => {
        pts.push([s.lat, s.lng]);
        const color = s.late ? '#e5484d' : '#2d6cdf';
        L.circleMarker([s.lat, s.lng],
          {radius: 8, color: color, fillColor: color, fillOpacity: 1})
          .addTo(layer).bindPopup(`${s.sequence}. ${esc(s.name)} — ETA ${s.eta}`);

        let cls = 'stop';
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
        L.polyline(pts, {color: '#2d6cdf', weight: 3, opacity: 0.7}).addTo(layer);
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

if __name__ == "__main__":
    app.run(debug=True, port=5000)