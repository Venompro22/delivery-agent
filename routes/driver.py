"""Drivers: the driver list and approval, the driver's phone screen, GPS uploads and the minute GPS / late-order watch."""
from __future__ import annotations

from datetime import datetime
from flask import request, jsonify, render_template, session
from models import OrderStatus
from optimizer import optimize_route
from users import public
import os
import time

import fileapp as core


@core.app.route("/drivers")
@core.owner_required
def drivers_page():
    return render_template("drivers.html")


@core.app.route("/api/drivers")
@core.owner_required
def list_drivers():
    """👤 Every driver account (never the password hash), pending ones first."""
    # Collect every rating per driver in one pass.
    ratings: dict[str, list[int]] = {}
    for o in core.orders_for_request():
        if o.driver_id and o.rating:
            ratings.setdefault(o.driver_id, []).append(o.rating)

    result = []
    for u in core.users.all():
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


@core.app.route("/api/drivers/<user_id>/status", methods=["POST"])
@core.owner_required
def set_driver_status(user_id):
    """✅ Approve or ⛔ block a driver account."""
    status = str((request.get_json(silent=True) or {}).get("status", ""))
    if status not in ("approved", "blocked"):
        return core.bad_request("status must be approved or blocked")
    if not core.users.set_status(user_id, status):
        return jsonify({"ok": False}), 404
    print(f"👤 Driver {user_id} → {status}", flush=True)
    u = core.users.get(user_id)
    core.audit.add(status, core.client_ip(), "owner", f"{u['name']} ({u['phone']})" if u else user_id)
    return jsonify({"ok": True})


@core.app.route("/api/drivers/<user_id>/reset-password", methods=["POST"])
@core.owner_required
def reset_driver_password(user_id):
    """🔑 Owner gives a driver a temporary password (shown once)."""
    temp = core.users.reset_password(user_id)
    if not temp:
        return jsonify({"ok": False}), 404
    u = core.users.get(user_id)
    core.audit.add("password_reset", core.client_ip(), "owner", f"{u['name']} ({u['phone']})")
    res = jsonify({"ok": True, "password": temp})
    res.headers["Cache-Control"] = "no-store"     # never keep a password in any cache
    return res


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


@core.app.route("/api/driver/location", methods=["POST"])
@core.login_required
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
            return core.bad_request(f"send between 1 and {GPS_MAX_POINTS} points")
        now = time.time()
        good = sorted((c for c in (_clean_gps_point(p, now) for p in points) if c), key=lambda c: c[2])
        rejected = len(points) - len(good)
        if not good:
            return core.bad_request("no valid points")
        lat, lng, at = good[-1]
    else:
        body = body if isinstance(body, dict) else {}
        try:
            lat = float(body.get("lat"))
            lng = float(body.get("lng"))
        except (TypeError, ValueError):
            return core.bad_request("lat and lng must be numbers")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return core.bad_request("lat/lng out of range")
        good, at = [(lat, lng, None)], None
    uid = session.get("uid")
    if uid:
        core.users.set_location(uid, lat, lng, at)    # 📍 this driver's own position (newest point)
        for p_lat, p_lng, p_at in good:          # every good point goes to the trail
            core._remember_trail(uid, p_lat, p_lng, p_at)
    else:
        session["here"] = [lat, lng]             # this browser's start point (survives restarts)
        if core.current_role() == "owner":
            core.driver.lat, core.driver.lng = lat, lng    # default start for drivers without GPS
            core._remember_trail("default", lat, lng)
        core._move_demo_near(lat, lng)
    return jsonify({"ok": True, "accepted": len(good), "rejected": rejected})


@core.app.route("/api/driver/mark-seen", methods=["POST"])
@core.login_required
def mark_orders_seen():
    """The driver has seen their orders — clear the 'new' badge."""
    uid = session.get("uid")
    if uid:
        core.users.mark_seen(uid)
    return jsonify({"ok": True})
    return jsonify({"ok": True, "lat": lat, "lng": lng})


@core.app.route("/api/driver/today")
@core.login_required
def driver_today():
    """The driver's own day: deliveries done and money collected (never another driver's)."""
    today = datetime.now().date()
    uid = session.get("uid")
    done = [o for o in core.my_orders()
            if o.status == OrderStatus.DELIVERED and o.delivered_at
            and datetime.fromtimestamp(o.delivered_at).date() == today
            and (not uid or o.driver_id == uid)]
    paid = lambda kinds: round(sum(o.price or 0 for o in done if o.paid_at and o.pay_method in kinds), 2)
    return jsonify({"ok": True, "delivered": len(done), "cash": paid(("cash",)),
                    "online": paid(("wechat", "alipay")),
                    "to_collect": round(sum(o.price or 0 for o in done if o.price and not o.paid_at), 2)})


@core.app.route("/driver")
@core.login_required
def driver_page():
    return render_template("driver.html", role=core.current_role())


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
    sent, out = core._watch["sent"], []
    open_orders = [o for o in core.store.all()
                   if o.status == OrderStatus.PENDING and not (o.note or "").startswith(core.DEMO_TAG)]
    for u in core.users.all():
        if u.get("role", "driver") != "driver" or u.get("status") != "approved":
            continue
        uid, name = u["id"], u["name"]
        mine = [o for o in open_orders if o.driver_id == uid]
        if not mine or u.get("lat") is None:            # not working: forget old state
            core._watch["still"].pop(uid, None)
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
            prev = core._watch["still"].get(uid)
            if not prev or haversine_km(prev[0], prev[1], here[0], here[1]) * 1000 > WATCH_STILL_M:
                core._watch["still"][uid] = (here[0], here[1], now)
                sent.discard(f"still:{uid}")
            else:
                at_door = any(haversine_km(o.lat, o.lng, here[0], here[1]) <= WATCH_DOOR_KM for o in mine)
                if now - prev[2] > WATCH_STILL_S and not at_door and f"still:{uid}" not in sent:
                    sent.add(f"still:{uid}")
                    out.append(f"Driver stopped: {name} has not moved for {int((now - prev[2]) // 60)} min "
                               f"({len(mine)} orders open)")

    # 3) An open order that will arrive after its promise: one alert per order, ever
    for late in core.late_now(open_orders, now):
        if f"late:{late['id']}" not in sent:
            sent.add(f"late:{late['id']}")
            out.append(f"Running late: {late['customer']} ({late['driver'] or 'no driver'}) "
                       f"~{late['minutes_late']} min late, expected {core.fmt(late['eta'])}, "
                       f"promised {core.fmt(late['promised'])}, order {late['id']}")
    return out


def late_now(orders=None, now=None) -> list:
    """Open orders whose predicted ETA is after their promise (or the promise already passed),
    worst first. Same routing as each driver's screen: a driver's open orders from their GPS
    position (or the default start), unassigned orders from the default start."""
    import math
    now = now or time.time()
    by_driver = {}
    for o in (core.orders_for_request() if orders is None else orders):
        if o.is_open:
            by_driver.setdefault(o.driver_id, []).append(o)
    out = []
    for uid, mine in by_driver.items():
        user = core.users.get(uid) if uid else None
        # Orders without a promise still ride along: they push the others' ETAs back.
        for o in optimize_route(core.driver_for(uid), mine, core.geo, start_time=now):
            if o.sequence != 1:
                core._NOT_NEXT_AT[o.id] = now         # trail safety: this order is not being driven to
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
            for msg in core.gps_watch():
                alerts._telegram(msg)
        except Exception as e:                    # never let the watch crash the app
            print(f"GPS watch failed: {e}", flush=True)


import sys as _sys  # noqa: E402
if os.environ.get("GPS_WATCH", "1") == "1" and "pytest" not in _sys.modules:
    import threading as _threading  # noqa: E402
    _threading.Thread(target=_gps_watch_loop, daemon=True, name="gps-watch").start()
