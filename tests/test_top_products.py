"""Stats: best-selling products of the last 7 days, from delivered customer orders."""
import json
import time

import fileapp
from conftest import login
from models import Order, OrderStatus


def delivered(items, days_ago=0, status=OrderStatus.DELIVERED):
    now = time.time() - days_ago * 86400
    o = Order(customer_name="Kehu", lat=30.25, lng=119.72, status=status,
              items=json.dumps(items) if not isinstance(items, str) else items)
    o.created_at = now - 600
    o.delivered_at = now if status == OrderStatus.DELIVERED else None
    fileapp.store.add(o)
    return o


WATER = {"id": "p1", "name": "桶装水", "unit": "桶", "price": 18, "qty": 2}
TEA = {"id": "p2", "name": "茶", "unit": "", "price": 4.5, "qty": 1}


def top(client):
    login(client, "owner-pass")
    return client.get("/api/stats").get_json()["top_products"]


def test_best_seller_comes_first_with_totals(client):
    delivered([WATER, TEA])
    delivered([{**WATER, "qty": 3}])
    t = top(client)
    assert [p["name"] for p in t] == ["桶装水", "茶"]
    assert t[0]["qty"] == 5 and t[0]["orders"] == 2 and t[0]["revenue"] == 90.0
    assert t[0]["share"] == 83 and t[0]["unit"] == "桶"


def test_only_delivered_orders_of_the_last_week_count(client):
    delivered([WATER], days_ago=10)
    delivered([TEA], status=OrderStatus.PENDING)
    assert top(client) == []


def test_broken_item_data_is_skipped(client):
    delivered("not json")
    delivered([{"name": "", "qty": 1}, {"name": "x", "qty": "many"}, {"name": "y", "qty": 0}, TEA])
    assert [p["name"] for p in top(client)] == ["茶"]


def test_stats_page_has_the_section(client):
    login(client, "owner-pass")
    assert "product-stats" in client.get("/stats").get_data(as_text=True)
