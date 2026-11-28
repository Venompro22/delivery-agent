"""Customer side: the /me screen and its APIs, public tracking pages, and the per-order driver trail."""
from __future__ import annotations

from collections import OrderedDict
from collections import deque
from flask import request, jsonify, render_template, session, redirect, url_for
from geo import wgs84_to_gcj02, gcj02_to_wgs84
from models import Order, OrderStatus
from optimizer import optimize_route
from settings import delivery_fee_for
import hmac
import json
import os
import owner_ai
import re
import requests
import time

import fileapp as core


@core.app.route("/me")
@core.login_required
def my_page():
    """🧾 The customer's own dashboard: their orders on a map."""
    if core.current_role() != "customer":
        return redirect(url_for("home"))
    me = core.users.get(session.get("uid")) or {}
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
    return {"id": order.id, "name": order.customer_name, "note": core.public_note(order),
            "status": order.status.value, "created_at": order.created_at,
            "lat": order.lat, "lng": order.lng, "price": order.price, "items": _order_items(order),
            "has_trail": order.status == OrderStatus.DELIVERED and bool(order.trail),
            "track_url": f"/track/{order.id}?k={core.track_key(order.id)}"}


@core.app.route("/api/me/orders")
@core.login_required
def my_orders_api():
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    orders = [core.store.get(i) for i in core.users.customer_order_ids(session["uid"])]
    return jsonify({"ok": True, "orders": [customer_order_view(o) for o in orders if o]})


@core.app.route("/api/me/orders/<order_id>/trail")
@core.login_required
def my_order_trail(order_id):
    """The path the driver took for one of MY delivered orders. Any other order: the same 404."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    o = core.store.get(order_id) if order_id in core.users.customer_order_ids(session["uid"]) else None
    if not o or o.status != OrderStatus.DELIVERED:
        return jsonify({"ok": False, "error": "order not found"}), 404
    try:
        trail = json.loads(o.trail) if o.trail else []
    except ValueError:
        trail = []
    return jsonify({"ok": True, "id": o.id, "lat": o.lat, "lng": o.lng,
                    "delivered_at": o.delivered_at, "trail": trail})


@core.app.route("/api/me/claim", methods=["POST"])
@core.login_required
def claim_order():
    """Add an order to my account with its tracking link (the signed key proves it's mine)."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    link = (request.get_json(silent=True) or {}).get("link", "")
    m = re.search(r"/track/([A-Za-z0-9]+)\?k=([A-Za-z0-9_\-]+)", link)
    if not m or not core.store.get(m.group(1)) or \
            not hmac.compare_digest(m.group(2).encode(), core.track_key(m.group(1)).encode()):
        return jsonify({"ok": False, "error": "this tracking link is not valid"}), 400
    core.users.link_order(session["uid"], m.group(1))
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
    catalog = {p["id"]: p for p in core.product_store().all()}      # active products only
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


@core.app.route("/api/me/order", methods=["POST"])
@core.login_required
def customer_place_order():
    """A customer orders from the catalog. The server computes the total; the shop confirms it."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    uid = session["uid"]
    me = core.users.get(uid) or {}
    body = request.get_json(silent=True) or {}

    # 1) Items: visible products and whole quantities, priced from the catalog.
    items, total, error = core.catalog_items(body.get("items"))
    if error:
        return core.bad_request(error)

    # 1b) Shop rules: minimum order and delivery fee (never trusted from the browser).
    shop = core.settings_store().all()
    if total < shop["min_order"]:
        return core.bad_request(f"minimum order is {shop['min_order']:g} yuan")
    fee = delivery_fee_for(total, shop)

    # 2) Where: a point on the map plus building / floor details.
    try:
        lat, lng = float(body.get("lat")), float(body.get("lng"))
    except (TypeError, ValueError):
        return core.bad_request("tap the map to set your location")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):      # also rejects nan
        return core.bad_request("tap the map to set your location")
    address = str(body.get("address", "")).strip()
    if not address:
        return core.bad_request("add your address details (building, floor, room)")
    if len(address) > MAX_ADDRESS:
        return core.bad_request(f"address is too long (max {MAX_ADDRESS} characters)")

    # 3) Anti-spam: a few waiting orders at most, and a few per hour.
    now = time.time()
    mine = [o for o in (core.store.get(i) for i in core.users.customer_order_ids(uid)) if o]
    if sum(1 for o in mine if o.status == OrderStatus.REQUESTED) >= core.MAX_OPEN_REQUESTS:
        return core.bad_request("you already have orders waiting for the shop")
    if sum(1 for o in mine if (o.created_at or 0) > now - 3600) >= MAX_REQUESTS_PER_HOUR:
        return jsonify({"ok": False, "error": "too many orders, please try again later"}), 429

    demo = bool(session.get("demo"))     # demo customer: the order stays in the demo sandbox
    summary = ", ".join(f"{i['name']} x{i['qty']}" for i in items)
    if fee:
        summary += f", delivery {fee:g}"
    lat, lng, _ = core._snap_to_learned(me.get("phone", ""), lat, lng)
    order = Order(customer_name=me.get("name") or "Customer", lat=lat, lng=lng,
                  phone=me.get("phone", ""), note=((core.DEMO_TAG if demo else "") + f"{address} | {summary}")[:200],
                  price=round(total + fee, 2), status=OrderStatus.PENDING if demo else OrderStatus.REQUESTED,
                  items=json.dumps(items, ensure_ascii=False))
    core._store_add(order)
    core.users.link_order(uid, order.id)
    if not demo:
        core._notify_new_request(order, summary)   # demo orders never ping the shop
    return jsonify({"ok": True, "id": order.id, "total": order.price, "fee": fee,
                    "track_url": f"/track/{order.id}?k={core.track_key(order.id)}"})


@core.app.route("/api/me/ai-order", methods=["POST"])
@core.login_required
def customer_ai_order():
    """Read a customer's message ("两桶水送到5号楼302") into a cart suggestion.
    Nothing is ordered here: the page shows the cart and the customer sends it as usual."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    if not text:
        return core.bad_request("text is required")
    if len(text) > 300:
        return core.bad_request("text is too long")
    from order_parser import match_products
    catalog = core.product_store().all()
    found = match_products(text, catalog)
    by_id = {p["id"]: p for p in catalog}
    items = [{**{k: by_id[i["id"]][k] for k in ("id", "name", "unit", "price")}, "qty": i["qty"]}
             for i in found["items"]]
    address = (found["address"] or "")[:MAX_ADDRESS]
    # Reply language follows the message: Chinese characters → Chinese, anything else → English
    return jsonify({"ok": True, "items": items, "address": address, "unmatched": found["unmatched"],
                    "lang": owner_ai.reply_lang(text)})


@core.app.route("/api/me/orders/<order_id>/cancel", methods=["POST"])
@core.login_required
def customer_cancel_order(order_id):
    """A customer can take back an order while the shop has not confirmed it yet."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    order = core.store.get(order_id)
    if not order or order_id not in core.users.customer_order_ids(session["uid"]):
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.REQUESTED:
        return core.bad_request("the shop already confirmed this order, please contact them")
    order.status = OrderStatus.CANCELLED
    core.store.save()
    return jsonify({"ok": True})


@core.app.route("/api/me/shop")
@core.login_required
def shop_info():
    """What customers need to know before ordering: delivery fee and minimum order."""
    return jsonify(core.settings_store().all())


def public_note(order) -> str:
    """The order note as the customer should see it (internal demo tag removed)."""
    note = order.note or ""
    return note[len(core.DEMO_TAG):].strip() if note.startswith(core.DEMO_TAG) else note.strip()


@core.app.route("/api/track/<order_id>")
def api_track(order_id):
    """Public info for ONE order — safe to share with the customer."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, core.track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = core.store.get(order_id)
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
    u = core.users.get(order.driver_id) or {}
    if u.get("lat") is None or time.time() - (u.get("located_at") or 0) > core.ARRIVED_FRESH_S:
        return False
    return haversine_km(u["lat"], u["lng"], order.lat, order.lng) <= ARRIVED_KM


def track_payload(order_id, order) -> dict:
    """What the tracking page shows for one order (without the shop contact)."""
    if order.status == OrderStatus.DELIVERED:
        return ({"ok": True, "status": "delivered", "name": order.customer_name, "rating": order.rating,
                        "has_proof": bool(order.proof), "note": core.public_note(order),
                        "created_at": order.created_at, "delivered_at": getattr(order, "delivered_at", None)})
    if order.status == OrderStatus.CANCELLED:
        return ({"ok": True, "status": "cancelled", "name": order.customer_name})
    if order.status == OrderStatus.REQUESTED:
        return ({"ok": True, "status": "requested", "name": order.customer_name,
                 "note": core.public_note(order), "created_at": order.created_at, "price": order.price})

    # Where is this order in the current route?
    same_driver = [o for o in core.orders_for_request() if o.driver_id == order.driver_id]
    route = optimize_route(core.driver_for(order.driver_id), same_driver, core.geo)
    me = next((o for o in route if o.id == order_id), None)
    return ({
        "ok": True,
        "status": "on_the_way",
        "arrived": driver_has_arrived(order, me),
        "name": order.customer_name,
        "position": me.sequence if me else None,
        "stops_before": (me.sequence - 1) if me else None,
        "eta": core.fmt(me.eta_epoch) if me else "--",
        "promise": core.fmt(order.promised_by) if order.promised_by else None,
        "eta_epoch": me.eta_epoch if me else None,     # raw seconds, for the live countdown
        "promised_epoch": order.promised_by,
        "created_at": order.created_at,
        "note": core.public_note(order),
        "lat": order.lat,
        "lng": order.lng,
    })


@core.app.route("/api/track/<order_id>/rate", methods=["POST"])
def api_rate(order_id):
    """The customer rates their delivery (1-5). Public, but needs the signed key."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, core.track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = core.store.get(order_id)
    if not order:
        return jsonify({"ok": False, "error": "order not found"}), 404
    if order.status != OrderStatus.DELIVERED:
        return core.bad_request("you can only rate a delivered order")
    if order.rating is not None:
        return core.bad_request("this order is already rated")
    body = request.get_json(silent=True) or {}
    try:
        stars = int(body.get("rating"))
    except (TypeError, ValueError):
        return core.bad_request("rating must be a number")
    if not 1 <= stars <= 5:
        return core.bad_request("rating must be between 1 and 5")
    order.rating = stars
    core.store.save()
    print(f"⭐ Rated: {order.customer_name} → {stars}/5", flush=True)
    return jsonify({"ok": True, "rating": stars})


@core.app.route("/api/track/<order_id>/proof")
def track_proof(order_id):
    """📷 Serve the delivery photo to the customer — needs the signed key, no login."""
    given = request.args.get("k", "").encode()
    if not hmac.compare_digest(given, core.track_key(order_id).encode()):
        return jsonify({"ok": False, "error": "order not found"}), 404   # same answer: no hints
    order = core.store.get(order_id)
    if not order or not order.proof:
        return jsonify({"ok": False, "error": "no photo"}), 404
    # send_from_directory is safe: it refuses paths that escape PROOF_DIR.
    return core.proof_response(order.proof)


@core.app.route("/track/<order_id>")
def track_page(order_id):
    return render_template("track.html", order_id=order_id, shop_name=os.environ.get("SHOP_NAME", "智送管家"))


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
    q = core._TRAILS.setdefault(key, deque(maxlen=TRAIL_MAX_POINTS))
    at = at or time.time()
    if q and at < q[-1][0]:
        return                                   # older than the trail's end: would draw a zigzag
    if not q or abs(q[-1][1] - lat) > 1e-5 or abs(q[-1][2] - lng) > 1e-5:
        q.append((at, lat, lng))


def _previous_door(order, now):
    """(lat, lng) of the driver's previous delivery (the last 30 min), or None. Never shown:
    the driver marker and the trail stay hidden near it, so it never reveals that address."""
    done = [o for o in core.store.all() if o.driver_id == order.driver_id and o.id != order.id
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
    last_done = max((o.delivered_at or 0 for o in core.store.all()
                     if o.driver_id == order.driver_id and o.id != order.id
                     and o.status == OrderStatus.DELIVERED), default=0)
    return max(last_done, order.created_at or 0, core._NOT_NEXT_AT.get(order.id, 0))


def _order_trail(order, until) -> list:
    """The assigned driver's path for THIS order only, without the previous customer's door."""
    from geo import haversine_km
    if not order.driver_id:
        return []                                # pool order: nobody is driving to it yet
    since = max(core._trail_start(order), until - TRAIL_KEEP_S)
    door = _previous_door(order, until)
    pts = [(p[1], p[2]) for p in core._TRAILS.get(order.driver_id, ())
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
    core._NOT_NEXT_AT.pop(order.id, None)


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


@core.app.route("/api/me/live")
@core.login_required
def my_live():
    """The customer's open orders: where the driver is, and the road only once they are next."""
    if core.current_role() != "customer":
        return jsonify({"ok": False, "error": "customers only"}), 403
    now = time.time()
    items = []
    for oid in core.users.customer_order_ids(session["uid"]):
        o = core.store.get(oid)
        if not o or o.status not in (OrderStatus.PENDING, OrderStatus.REQUESTED):
            continue
        item = {"id": o.id, "status": o.status.value, "lat": o.lat, "lng": o.lng, "price": o.price,
                "note": core.public_note(o), "track_url": f"/track/{o.id}?k={core.track_key(o.id)}"}
        if o.status == OrderStatus.PENDING:
            same = [x for x in core.store.all() if x.driver_id == o.driver_id]
            me = next((x for x in optimize_route(core.driver_for(o.driver_id), same, core.geo, start_time=now)
                       if x.id == o.id), None)
            # 🛡️ The driver, their road and their trail only for an assigned order that is the
            # driver's next stop. Before that: how many stops, never where the driver is.
            # A demo visitor's unassigned demo order is driven by the demo driver (the shop's
            # default point, not a person), so the demo still shows a road at once.
            demo = o.driver_id is None and (o.note or "").startswith(core.DEMO_TAG)
            is_next = bool(me and me.sequence == 1 and (o.driver_id or demo))
            if not is_next:
                core._NOT_NEXT_AT[o.id] = now
            u = core.users.get(o.driver_id) if is_next and o.driver_id else None
            drv = ([u["lat"], u["lng"]] if u and u.get("lat") is not None
                   else [core.driver.lat, core.driver.lng] if is_next and demo else None)
            if drv and o.driver_id and _near(_previous_door(o, now), drv):
                drv = None                       # still at the previous customer's door: not shown
            item.update({
                "eta": core.fmt(me.eta_epoch) if me else "--",
                "eta_epoch": me.eta_epoch if me else None,
                "stops_before": (me.sequence - 1) if me else None,
                "promise": core.fmt(o.promised_by) if o.promised_by else None,
                "arrived": driver_has_arrived(o, me),
                "next": is_next,
                "driver": drv,
                "driver_age_s": round(now - (u.get("located_at") or 0)) if u and drv else None,
                "road": core._road_line(tuple(drv), (o.lat, o.lng)) if drv else [],
                "trail": _order_trail(o, now) if drv else [],
            })
        items.append(item)
    return jsonify({"ok": True, "orders": items})
