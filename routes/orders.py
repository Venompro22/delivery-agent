"""Owner order work: the dashboard and route, adding/editing/delivering orders, proofs, payments, products and shop settings."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from flask import request, jsonify, render_template, session
from geo import wgs84_to_gcj02, gcj02_to_wgs84
from models import Order, OrderStatus
from optimizer import optimize_route, route_summary
from products import clean as clean_product
from settings import clean as clean_settings, delivery_fee_for
from users import find_nearest_driver
import json
import os
import re
import requests
import time

import fileapp as core


@core.app.route("/api/orders/<order_id>/confirm", methods=["POST"])
@core.owner_required
def confirm_order(order_id):
    """The shop accepts a customer's order: it joins the route like any other order."""
    order = core.store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.REQUESTED:
        return core.bad_request("this order is not waiting for confirmation")
    order.status = OrderStatus.PENDING
    nearest = find_nearest_driver(core.users, order.lat, order.lng)
    if nearest and not order.driver_id:
        order.driver_id = nearest["id"]
    core.store.save()
    return jsonify({"ok": True})


_ROAD_CACHE: "OrderedDict[str, list]" = OrderedDict()


@core.app.route("/api/road")
@core.login_required
def road_path():
    """🛣️ The road between points, so routes follow streets instead of straight lines.
    Points come and go in GCJ-02 (Gaode); OSRM works in WGS-84. Cached. If OSRM is slow
    or down we answer the straight line, so the map never breaks."""
    try:
        pts = [tuple(float(v) for v in p.split(",")) for p in request.args.get("pts", "").split(";") if p]
    except ValueError:
        return core.bad_request("pts must look like lat,lng;lat,lng")
    if not 2 <= len(pts) <= 25 or any(len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180) for p in pts):
        return core.bad_request("send 2 to 25 valid points")
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


@core.app.route("/api/customers/quiet")
@core.owner_required
def quiet_customers():
    """🧠 Regulars who went quiet (usually every ~7 days, nothing for 14): bring them back."""
    import customers
    return jsonify({"ok": True, "customers": customers.quiet(core.orders_for_request(), time.time())[:10]})


@core.app.route("/")
@core.owner_required
def home():
    return render_template("dashboard.html", is_owner=core.current_role() == "owner")


@core.app.route("/api/route")
@core.login_required
def api_route():
    mine = core.my_orders()
    me = core.driver_for(session.get("uid"))
    route = optimize_route(me, mine, core.geo)
    summary = route_summary(route)
    late_ids = set(summary.get("late_orders", []))
    delivered = sum(1 for o in mine if o.status == OrderStatus.DELIVERED)
    # Badge: orders assigned to me that arrived after I last looked.
    new_count = 0
    uid = session.get("uid")
    if uid and core.current_role() != "owner":
        seen = core.users.get_seen_at(uid)
        new_count = sum(1 for o in mine
                        if o.driver_id == uid
                        and (o.created_at or 0) > seen
                        and o.status == OrderStatus.PENDING)
    data = {
        "driver": {"lat": me.lat, "lng": me.lng, "name": me.name},
        "summary": {
            "stops": summary["stops"],
            "total_km": summary["total_km"],
            "finish": core.fmt(summary.get("finish_epoch")),
            "late_count": len(late_ids),
            "delivered": delivered,
            "new_count": new_count,
            "requested_count": sum(1 for o in core.orders_for_request() if o.status == OrderStatus.REQUESTED)
                               if core.current_role() == "owner" else 0,
        },
        "stops": [
            {
                "sequence": o.sequence,
                "id": o.id,
                "track_key": core.track_key(o.id),
                "driver_id": o.driver_id,
                "name": o.customer_name,
                "phone": o.phone,
                "note": o.note,
                "lat": o.lat,
                "lng": o.lng,
                "eta": core.fmt(o.eta_epoch),
                "leg_km": o.leg_distance_km,
                "promise": core.fmt(o.promised_by) if o.promised_by else None,
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
    if core.current_role() == "owner":                 # 🗺️ every driver on the owner's map
        now = time.time()
        data["drivers"] = [
            {"id": u["id"], "name": u["name"], "lat": u["lat"], "lng": u["lng"],
             "age": round(now - (u["located_at"] or 0))}
            for u in core.users.all() if u["status"] == "approved" and u.get("lat") is not None]
    return jsonify(data)


@core.app.route("/api/orders", methods=["POST"])
@core.owner_required
def add_order():
    body = request.get_json(silent=True) or {}

    # 1) Required fields must not be empty.
    name = str(body.get("name", "")).strip()
    lat_raw = str(body.get("lat", "")).strip()
    lng_raw = str(body.get("lng", "")).strip()
    if not name or not lat_raw or not lng_raw:
        return core.bad_request("name, lat and lng are required")

    # 2) lat/lng must be real numbers in a valid range.
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except ValueError:
        return core.bad_request("lat and lng must be numbers")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return core.bad_request("lat/lng out of range")

    # 3) Phone is optional, but if given it must look like a phone number.
    phone = str(body.get("phone", "")).strip()
    if phone and not re.fullmatch(r"[0-9+\-\s()]{6,20}", phone):
        return core.bad_request("phone looks invalid")

    # 4) Note (address details) is optional, max 200 characters.
    note = str(body.get("note", "")).strip()
    if len(note) > 200:
        return core.bad_request("note is too long (max 200 characters)")

    # 4b) Price is optional: the order total in yuan.
    price = None
    price_raw = str(body.get("price", "")).strip()
    if price_raw:
        try:
            price = round(float(price_raw), 2)
        except ValueError:
            return core.bad_request("price must be a number")
        if not (0 <= price <= 100000):            # also rejects nan and inf
            return core.bad_request("price is out of range")

    # 5) Promise is optional, but if given it must be a positive number.
    kw = {}
    promise_raw = str(body.get("promise_minutes", "")).strip()
    if promise_raw:
        try:
            minutes = float(promise_raw)
        except ValueError:
            return core.bad_request("promise must be a number of minutes")
        if minutes <= 0:
            return core.bad_request("promise must be positive")
        kw["promised_by"] = time.time() + minutes * 60

    # 6) Products are optional. When picked, the server prices them from the catalog plus the
    #    shop's delivery fee, and any price typed in the browser is ignored.
    raw_items = body.get("items")
    if raw_items not in (None, []):
        items, subtotal, error = core.catalog_items(raw_items)
        if error:
            return core.bad_request(error)
        fee = delivery_fee_for(subtotal, core.settings_store().all())
        price = round(subtotal + fee, 2)
        summary = ", ".join(f"{i['name']} x{i['qty']}" for i in items) + (f", delivery {fee:g}" if fee else "")
        note = (f"{note} | {summary}" if note else summary)[:200]
        kw["items"] = json.dumps(items, ensure_ascii=False)

    lat, lng, snapped = _snap_to_learned(phone, lat, lng)
    order = Order(customer_name=name, lat=lat, lng=lng, phone=phone, note=note, price=price, **kw)
    # Auto-assign to the nearest approved driver that has a GPS position.
    # If none is available, driver_id stays None and the order sits in the pool.
    nearest = find_nearest_driver(core.users, order.lat, order.lng)
    if nearest:
        order.driver_id = nearest["id"]
    core._store_add(order)
    url = f"{request.host_url.rstrip('/')}/track/{order.id}?k={core.track_key(order.id)}"
    reply = f"好的，订单收到啦！点这里实时查看配送进度：{url}"
    return jsonify({"ok": True, "id": order.id, "track_url": url, "reply": reply, "snapped": snapped})


@core.app.route("/api/orders/<order_id>/deliver", methods=["POST"])
@core.login_required
def deliver_order(order_id):
    order = core.store.get(order_id)
    if order and core.visible_to_me(order):            # 🛡️ never someone else's order (IDOR)
        if order.status != OrderStatus.PENDING:
            return core.bad_request("order is not open")
        if order.driver_id is None and session.get("uid"):
            order.driver_id = session["uid"]      # a pool order belongs to whoever delivers it
        core._mark_delivered(order)
        core.store.save()
        core._learn_place(order)
        print(f"✅ Delivered: {order.customer_name}", flush=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

PAY_METHODS = ("cash", "wechat", "alipay")


@core.app.route("/api/orders/<order_id>/paid", methods=["POST"])
@core.login_required
def mark_paid(order_id):
    """Record that the customer paid (cash / wechat / alipay), or undo it with "none"."""
    order = core.store.get(order_id)
    if not order or not core.visible_to_me(order):      # never someone else's order (IDOR)
        return jsonify({"ok": False}), 404
    method = str((request.get_json(silent=True) or {}).get("method", "")).strip().lower()
    if method == "none":
        order.pay_method, order.paid_at = "", None
    elif method in core.PAY_METHODS:
        order.pay_method, order.paid_at = method, time.time()
    else:
        return core.bad_request("method must be cash, wechat, alipay or none")
    core.store.save()
    return jsonify({"ok": True, "paid": order.paid_at is not None, "method": order.pay_method})


@core.app.route("/api/orders/<order_id>/proof", methods=["POST"])
@core.login_required
def upload_proof(order_id):
    """📷 The driver uploads a delivery photo. Stored outside static/, login-only."""
    order = core.store.get(order_id)
    if not order or not core.visible_to_me(order):     # 🛡️ never someone else's order (IDOR)
        return jsonify({"ok": False}), 404
    if order.status != OrderStatus.DELIVERED:
        return core.bad_request("photo is only for a delivered order")

    file = request.files.get("photo")
    if not file or not file.filename:
        return core.bad_request("no photo uploaded")

    ext = core.ALLOWED_PROOF_TYPES.get(file.mimetype)
    if not ext:
        return core.bad_request("photo must be JPEG, PNG or WebP")

    # Read with a hard size cap, so a huge file can't fill the disk.
    data = file.read(core.MAX_PROOF_BYTES + 1)
    if len(data) > core.MAX_PROOF_BYTES:
        return core.bad_request("photo is too large (max 5 MB)")

    filename = f"{order_id}{ext}"                 # order_id is a random uuid; never the user's name
    with open(os.path.join(core.PROOF_DIR, filename), "wb") as f:
        f.write(data)
    core.photo_store().put(filename, data, file.mimetype)
    order.proof = filename
    core.store.save()
    print(f"📷 Proof saved: {order.customer_name} ({len(data)} bytes)", flush=True)
    return jsonify({"ok": True})


@core.app.route("/api/orders/<order_id>/proof", methods=["GET"])
@core.login_required
def get_proof(order_id):
    """📷 Serve a delivery photo — login-only, and never someone else's order."""
    order = core.store.get(order_id)
    if not order or not core.visible_to_me(order):     # 🛡️ IDOR protection
        return jsonify({"ok": False}), 404
    if not order.proof:
        return jsonify({"ok": False, "error": "no photo"}), 404
    # send_from_directory is safe: it refuses paths that escape PROOF_DIR.
    return core.proof_response(order.proof)


@core.app.route("/api/orders/<order_id>/assign", methods=["POST"])
@core.owner_required
def assign_order(order_id):
    """👤 Give an order to a driver, or back to the pool (driver_id = null)."""
    order = core.store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    driver_id = (request.get_json(silent=True) or {}).get("driver_id")
    if driver_id is not None:
        u = core.users.get(str(driver_id))
        if not u or u["status"] != "approved":
            return core.bad_request("unknown or not approved driver")
        driver_id = u["id"]
    order.driver_id = driver_id
    core.store.save()
    print(f"👤 Assigned: {order.customer_name} → {driver_id or 'pool'}", flush=True)
    return jsonify({"ok": True})


@core.app.route("/api/orders/<order_id>/cancel", methods=["POST"])
@core.owner_required
def cancel_order(order_id):
    order = core.store.get(order_id)
    if not order:
        return jsonify({"ok": False}), 404
    if order.status == OrderStatus.DELIVERED:
        return core.bad_request("order is already delivered")
    order.status = OrderStatus.CANCELLED
    core.store.save()
    print(f"❌ Cancelled: {order.customer_name}", flush=True)
    return jsonify({"ok": True})

@core.app.route("/api/orders/<order_id>", methods=["PUT"])
@core.owner_required
def edit_order(order_id):
  order = core.store.get(order_id)
  if not order:
    return jsonify({"ok": False}), 404
  body = request.get_json(silent=True) or {}
  name = str(body.get("name", order.customer_name)).strip()
  phone = str(body.get("phone", order.phone)).strip()
  note = str(body.get("note", order.note)).strip()
  if not name:
    return core.bad_request("name is required")
  if phone and not re.fullmatch(r"[0-9+\-\s()]{6,20}", phone):
    return core.bad_request("phone looks invalid")
  if len(note) > 200:
    return core.bad_request("note is too long (max 200 characters)")
  if "price" in body:                         # empty value clears the price
    raw = str(body.get("price") or "").strip()
    if raw:
      try:
        new_price = round(float(raw), 2)
      except ValueError:
        return core.bad_request("price must be a number")
      if not (0 <= new_price <= 100000):
        return core.bad_request("price is out of range")
      order.price = new_price
    else:
      order.price = None
  order.customer_name = name
  order.phone = phone
  order.note = note
  core.store.save()
  print(f"✏️ Edited: {order.customer_name}", flush=True)
  return jsonify({"ok": True})


@core.app.route("/api/settings/shop")
@core.owner_required
def get_shop_settings():
    return jsonify(core.settings_store().all())


@core.app.route("/api/settings/shop", methods=["PUT"])
@core.owner_required
def update_shop_settings():
    values, error = clean_settings(request.get_json(silent=True) or {})
    if error:
        return core.bad_request(error)
    return jsonify({"ok": True, "settings": core.settings_store().update(**values)})


@core.app.route("/products")
@core.owner_required
def products_page():
    return render_template("products.html")


@core.app.route("/api/products")
@core.login_required
def list_products():
    """The catalog. The owner also sees hidden products (?all=1)."""
    show_hidden = core.current_role() == "owner" and request.args.get("all") == "1"
    return jsonify(core.product_store().all(include_hidden=show_hidden))


@core.app.route("/api/products", methods=["POST"])
@core.owner_required
def add_product():
    body = request.get_json(silent=True) or {}
    fields, error = clean_product(body.get("name", ""), body.get("price", ""), body.get("unit", ""))
    if error:
        return core.bad_request(error)
    product = core.product_store().create(fields["name"], fields["price"], fields["unit"])
    return jsonify({"ok": True, "product": product})


@core.app.route("/api/products/<product_id>", methods=["PUT"])
@core.owner_required
def edit_product(product_id):
    if not core.product_store().get(product_id):
        return jsonify({"ok": False}), 404
    body = request.get_json(silent=True) or {}
    fields, error = clean_product(body.get("name"), body.get("price"), body.get("unit"))
    if error:
        return core.bad_request(error)
    if "active" in body:
        fields["active"] = bool(body["active"])
    return jsonify({"ok": True, "product": core.product_store().update(product_id, **fields)})


@core.app.route("/orders")
@core.owner_required
def orders_page():
    return render_template("orders.html")


@core.app.route("/api/orders")
@core.owner_required
def list_orders():
    """Every order, newest first — delivered ones included."""
    today = datetime.now().date()
    items = []
    for o in sorted(core.orders_for_request(), key=lambda x: x.created_at or 0, reverse=True):
        created = datetime.fromtimestamp(o.created_at) if o.created_at else None
        items.append({
            "id": o.id,
            "track_key": core.track_key(o.id),
            "driver_id": o.driver_id,
            "name": o.customer_name,
            "phone": o.phone,
            "note": o.note,
            "lat": o.lat,
            "lng": o.lng,
            "status": o.status.value,
            "created": created.strftime("%m-%d %H:%M") if created else "--",
            "today": bool(created and created.date() == today),
            "promise": core.fmt(o.promised_by) if o.promised_by else None,
            "delivered": core.fmt(o.delivered_at) if o.delivered_at else None,
            "minutes": round((o.delivered_at - o.created_at) / 60)
                       if o.delivered_at and o.created_at else None,
            "has_proof": bool(o.proof),       # 📷 is there a delivery photo?
            "rating": o.rating,               # ⭐ customer rating, if any
            "price": o.price,                 # order total in yuan (None = not set)
            "pay_method": o.pay_method or "",
            "paid": o.paid_at is not None,
        })
    return jsonify(items)


LEARN_MAX_KM = 0.3     # driver GPS further than this from the pin: probably not at the door
SNAP_MAX_KM = 0.5      # new pin further than this from the learned spot: a different address


def _learn_place(order) -> None:
    """The driver just delivered: remember where they really were, for this customer's phone."""
    from geo import haversine_km
    if not order.phone or (order.note or "").startswith(core.DEMO_TAG) or not session.get("uid"):
        return
    u = core.users.get(session["uid"]) or {}
    if u.get("lat") is None or time.time() - (u.get("located_at") or 0) > core.ARRIVED_FRESH_S:
        return                                   # no fresh GPS: nothing reliable to learn
    if haversine_km(u["lat"], u["lng"], order.lat, order.lng) > LEARN_MAX_KM:
        return
    core.place_store().learn(order.phone, u["lat"], u["lng"])


def _snap_to_learned(phone, lat, lng):
    """Move a sloppy pin onto the spot drivers really delivered to. Returns (lat, lng, snapped)."""
    from geo import haversine_km
    spot = core.place_store().get(phone) if phone else None
    if spot and haversine_km(spot[0], spot[1], lat, lng) <= SNAP_MAX_KM:
        return round(spot[0], 6), round(spot[1], 6), True
    return lat, lng, False
