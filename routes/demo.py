"""Demo sandbox: sample orders and history, the /try entry points and the visitor's rough location."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from flask import request, jsonify, session, redirect, url_for, abort, has_request_context
from geo import wgs84_to_gcj02
from models import Order, OrderStatus
from optimizer import optimize_route
import hmac
import os
import requests
import secrets
import time

import fileapp as core


DEMO_FOLLOW_KM = 5    # GPS this far from today's demo orders: move them next to the visitor


def _demo_center() -> tuple:
    """Where demo orders go: this browser's GPS position, else the default start point."""
    here = session.get("here") if has_request_context() else None
    return (here[0], here[1]) if here else (core.driver.lat, core.driver.lng)


def _move_demo_near(lat: float, lng: float) -> None:
    """A visitor far from today's demo orders gets them rebuilt in their own city."""
    from geo import haversine_km
    live = [o for o in core.store.all()
            if (o.note or "").startswith(core.DEMO_TAG) and o.status == OrderStatus.PENDING]
    if live:
        c_lat = sum(o.lat for o in live) / len(live)
        c_lng = sum(o.lng for o in live) / len(live)
        if haversine_km(lat, lng, c_lat, c_lng) < DEMO_FOLLOW_KM:
            return
        core.store.orders = [o for o in core.store.orders
                        if not ((o.note or "").startswith(core.DEMO_TAG) and o.status == OrderStatus.PENDING)]
        core.store.save()
    seed_demo_orders(center=(lat, lng))


DEMO_ORDERS = [   # name, phone, address, promise in minutes (None = no promise), km north, km east
    ("张伟", "13800138001", "文三路100号 5号楼3楼", 40, 1.2, 0.8),
    ("李娜", "13900139002", "阳光小区 3单元 502室", 25, -0.9, 1.5),
    ("王芳", "15800158003", "图书馆 前台", None, 2.0, -1.1),
    ("刘洋", "13700137004", "西湖区 公司前台", 60, -1.6, -0.7),
    ("陈静", "18800188005", "学生宿舍 8号楼", 3, 0.5, 2.3),     # tight promise → shows LATE ⏰
    ("赵磊", "13600136006", "农贸市场 门口", None, 2.6, 1.9),
]


@core.app.route("/api/demo", methods=["POST"])
@core.owner_required
def demo_orders():
    """✨ Add realistic demo orders around the driver (great for showing the app)."""
    if not core.DEV_MODE:
        abort(404)
    import math
    ids = []
    c_lat, c_lng = _demo_center()
    for name, phone, note, promise, north_km, east_km in DEMO_ORDERS:
        lat = c_lat + north_km / 111.0
        lng = c_lng + east_km / (111.0 * math.cos(math.radians(c_lat)))
        kw = {"promised_by": time.time() + promise * 60} if promise else {}
        order = Order(customer_name=name, lat=round(lat, 5), lng=round(lng, 5),
                      phone=phone, note=core.DEMO_TAG + note, **kw)
        core._store_add(order)
        ids.append(order.id)
    print(f"✨ Demo: added {len(ids)} orders", flush=True)
    if not any((o.note or "").startswith(core.DEMO_TAG) and (o.created_at or 0) < time.time() - 2 * 86400
               for o in core.store.all()):
        seed_demo_history()  # owner pressed Demo: show the history too
    return jsonify({"ok": True, "ids": ids})


@core.app.route("/api/demo/clear", methods=["POST"])
@core.owner_required
def clear_demo():
    """🧹 Remove every demo order (real orders are never touched)."""
    if not core.DEV_MODE:
        abort(404)
    before = len(core.store.orders)
    core.store.orders = [o for o in core.store.orders if not (o.note or "").startswith(core.DEMO_TAG)]
    core.store.save()
    removed = before - len(core.store.orders)
    print(f"🧹 Demo: removed {removed} orders", flush=True)
    return jsonify({"ok": True, "removed": removed})


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
                      phone=phone, note=core.DEMO_TAG + note, **kw)
        core.store.add(order)
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
        lat = round(core.driver.lat + north / 111.0, 5)
        lng = round(core.driver.lng + east / (111.0 * math.cos(math.radians(core.driver.lat))), 5)
        qty = rnd.choice([1, 2, 2, 3])
        t = now - days * 86400 + rnd.uniform(0, every) * 86400
        while t < now - max(silent, 1) * 86400:
            created = datetime.fromtimestamp(t).replace(
                hour=rnd.choice([10, 11, 11, 12, 16, 17, 17, 18]), minute=rnd.randrange(60)).timestamp()
            if created < now - 3600:
                took = rnd.randint(18, 45)
                core.store.add(Order(customer_name=name, lat=lat, lng=lng, phone=phone,
                                note=f"{core.DEMO_TAG}{qty}桶 {place}", created_at=created,
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
    demo = [o for o in core.store.all() if (o.note or "").startswith(core.DEMO_TAG)]
    live = [o for o in demo if o.status == OrderStatus.PENDING]
    late = any(o.promised_by and o.promised_by < now - 1800 for o in live)
    stale = bool(live) and min(o.created_at or 0 for o in live) < now - DEMO_MAX_AGE
    if late or stale:
        core.store.orders = [o for o in core.store.orders
                        if not ((o.note or "").startswith(core.DEMO_TAG) and o.status == OrderStatus.PENDING)]
        core.store.save()
        live = []
    if not live:
        seed_demo_orders()
    if not any((o.created_at or now) < now - 2 * 86400 for o in demo):
        seed_demo_history()


@core.app.route("/try")
def try_demo():
    """👀 Public demo entrance from the login page. A sandbox: visitors only ever touch demo data."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = core.session_version()
    core.audit.add("demo_open", core.client_ip(), "viewer", "public")
    ensure_demo_data()
    return redirect(url_for("home"))


@core.app.route("/demo")
def demo_login():
    """👀 Signed demo link: opens the dashboard in the demo sandbox, no password needed."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, core.demo_key().encode()):
        return jsonify({"ok": False, "error": "not found"}), 404
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = core.session_version()          # "Logout everyone" ends demos too
    core.audit.add("demo_open", core.client_ip(), "viewer")
    ensure_demo_data()
    return redirect(url_for("home"))


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


@core.app.route("/api/where-am-i")
@core.login_required
def where_am_i():
    """Demo visitors without GPS land on their own city. Never moves the owner's real data."""
    if core.current_role() != "viewer" or session.get("here"):
        return jsonify({"ok": False})
    loc = core._ip_location(core.client_ip())
    if not loc:
        return jsonify({"ok": False})
    lat, lng = wgs84_to_gcj02(loc[0], loc[1])     # same coordinates as the Chinese map
    session["here"] = [lat, lng]
    _move_demo_near(lat, lng)
    return jsonify({"ok": True, "lat": lat, "lng": lng, "city": loc[2]})


# ---------- Separate demos: the driver's side and the customer's side ----------
def _open_demo_session() -> None:
    """A fresh demo visitor (viewer): sandboxed, only ever touches demo orders."""
    session.clear()
    session["auth"] = True
    session["role"] = "viewer"
    session["v"] = core.session_version()
    ensure_demo_data()


@core.app.route("/try/exit")
def try_exit():
    """Leave the demo and land on the real login page. Only demo sessions are cleared,
    so a link to this page can never log a real owner, driver or customer out."""
    if session.get("role") == "viewer" or session.get("demo"):
        session.clear()
    return redirect(url_for("login"))


@core.app.route("/try/driver")
def try_demo_driver():
    """Public demo of the driver's phone: next stop, Navigate, Delivered, live GPS."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    _open_demo_session()
    core.audit.add("demo_open", core.client_ip(), "viewer", "driver")
    return redirect("/driver")


DEMO_CUSTOMER_NAME = "体验用户 · demo"    # marks throwaway demo customer accounts


DEMO_CUSTOMER_TTL = 24 * 3600


def _cleanup_demo_customers() -> None:
    """Throwaway demo customer accounts live one day, then go (with their order links)."""
    con = core.users._connect()
    try:
        core.users._customer_orders_table(con)      # make sure it exists (a failed query aborts a PG transaction)
        old = [r[0] for r in con.execute("SELECT id FROM users WHERE name = ? AND created_at < ?",
                                         (DEMO_CUSTOMER_NAME, time.time() - core.DEMO_CUSTOMER_TTL)).fetchall()]
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
        if core.users.by_phone(phone):
            continue
        try:
            return core.users.create(phone, DEMO_CUSTOMER_NAME, "Demo!" + secrets.token_hex(10) + "Zz9",
                                role="customer")
        except ValueError:
            continue
    raise RuntimeError("could not create a demo customer")


@core.app.route("/try/customer")
def try_demo_customer():
    """Public demo of the customer's side: the full customer screen, with an order on its way."""
    if os.environ.get("PUBLIC_DEMO", "1") != "1":
        return redirect(url_for("login"))
    session.clear()
    ensure_demo_data()
    core._cleanup_demo_customers()
    me = _new_demo_customer()
    # Their order: the demo driver's next stop, so the road to their door shows at once
    live = [o for o in core.store.all() if (o.note or "").startswith(core.DEMO_TAG)
            and o.status == OrderStatus.PENDING and o.driver_id is None]
    route = optimize_route(core.driver_for(None), live, core.geo) if live else []
    if route:
        core.users.link_order(me["id"], route[0].id)
    session["auth"] = True
    session["role"] = "customer"
    session["uid"] = me["id"]
    session["pv"] = me.get("pw_version", 0)
    session["v"] = core.session_version()
    session["demo"] = True
    core.audit.add("demo_open", core.client_ip(), "viewer", "customer")
    return redirect("/me")
