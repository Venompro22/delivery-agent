"""Business numbers: /api/stats, the stats page and the learned ETA model."""
from __future__ import annotations

from datetime import datetime, timedelta
from eta_model import EtaModel, legs_from_orders
from flask import jsonify, render_template
from models import OrderStatus
import json
import optimizer as _optimizer_mod
import statistics
import time

import fileapp as core


@core.app.route("/api/stats")
@core.owner_required
def api_stats():
    """📊 Numbers for the stats page."""
    return jsonify(core.stats_data())


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

    for o in core.orders_for_request():
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
    names = {u["id"]: u["name"] for u in core.users.all()}
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


@core.app.route("/stats")
@core.owner_required
def stats_page():
    return render_template("stats.html")


ETA_RETRAIN_SECONDS = 30 * 60


_eta_cache = {"model": None, "at": 0.0, "store": None, "info": None}


def learned_eta_model():
    """Learned ETA model, retrained at most every 30 minutes. None = simple formula."""
    if time.time() - _eta_cache["at"] > ETA_RETRAIN_SECONDS or _eta_cache["store"] is not core.store:
        _eta_cache.update(at=time.time(), store=core.store)
        try:
            legs = legs_from_orders(
                core.store.all(),
                baseline=lambda a, b: core.geo.distance_time(a.lat, a.lng, b.lat, b.lng)[1] + b.service_minutes)
            model = EtaModel.train(legs)
            _eta_cache["model"] = model if (model and model.active) else None
            _eta_cache["info"] = model.info() if model else {"active": False, "legs": len(legs)}
        except Exception as e:                       # never break routing because of the model
            print(f"ETA model training failed: {e}", flush=True)
            _eta_cache["model"] = None
    return _eta_cache["model"]


_optimizer_mod.ETA_PROVIDER = learned_eta_model


@core.app.route("/api/eta-model")
@core.owner_required
def eta_model_info():
    """What the learned ETA model knows (Stats page)."""
    learned_eta_model()
    return jsonify(_eta_cache["info"] or {"active": False, "legs": 0})
