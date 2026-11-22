"""Money: each order can carry a price, be marked paid, and show up in revenue stats."""
import time

import fileapp
from conftest import login, login_driver
from models import Order, OrderStatus


def add_order(client, **extra):
    body = {"name": "Li", "lat": 30.25, "lng": 119.72, **extra}
    return client.post("/api/orders", json=body)


def test_new_order_keeps_its_price(client):
    login(client, "owner-pass")
    r = add_order(client, price="25.5")
    assert r.get_json()["ok"]
    order = fileapp.store.get(r.get_json()["id"])
    assert order.price == 25.5 and order.paid_at is None


def test_price_is_optional(client):
    login(client, "owner-pass")
    order = fileapp.store.get(add_order(client).get_json()["id"])
    assert order.price is None


def test_bad_prices_are_rejected(client):
    login(client, "owner-pass")
    for bad in ("abc", "-1", "nan", "inf", "999999"):
        assert add_order(client, price=bad).status_code == 400, bad


def test_edit_sets_and_clears_the_price(client):
    login(client, "owner-pass")
    oid = add_order(client).get_json()["id"]
    assert client.put(f"/api/orders/{oid}", json={"price": "18"}).get_json()["ok"]
    assert fileapp.store.get(oid).price == 18
    assert client.put(f"/api/orders/{oid}", json={"price": ""}).get_json()["ok"]
    assert fileapp.store.get(oid).price is None


def test_mark_paid_and_undo(client):
    login(client, "owner-pass")
    oid = add_order(client, price="30").get_json()["id"]
    r = client.post(f"/api/orders/{oid}/paid", json={"method": "wechat"}).get_json()
    assert r == {"ok": True, "paid": True, "method": "wechat"}
    assert fileapp.store.get(oid).paid_at is not None
    r = client.post(f"/api/orders/{oid}/paid", json={"method": "none"}).get_json()
    assert r == {"ok": True, "paid": False, "method": ""}


def test_unknown_pay_method_is_rejected(client):
    login(client, "owner-pass")
    oid = add_order(client).get_json()["id"]
    assert client.post(f"/api/orders/{oid}/paid", json={"method": "bitcoin"}).status_code == 400


def test_driver_cannot_mark_someone_elses_order(client):
    other = fileapp.store.add(Order(customer_name="X", lat=30.2, lng=120.1, driver_id="someone-else"))
    login_driver(client)
    assert client.post(f"/api/orders/{other.id}/paid", json={"method": "cash"}).status_code == 404
    assert fileapp.store.get(other.id).paid_at is None


def test_orders_list_shows_money(client):
    login(client, "owner-pass")
    oid = add_order(client, price="12").get_json()["id"]
    client.post(f"/api/orders/{oid}/paid", json={"method": "cash"})
    row = next(o for o in client.get("/api/orders").get_json() if o["id"] == oid)
    assert (row["price"], row["pay_method"], row["paid"]) == (12, "cash", True)


def test_stats_count_revenue_and_money_to_collect(client):
    login(client, "owner-pass")
    now = time.time()
    for price, paid in ((20, True), (15, False), (None, False)):
        fileapp.store.add(Order(customer_name="X", lat=30.2, lng=120.1, price=price,
                                status=OrderStatus.DELIVERED, created_at=now - 600,
                                delivered_at=now, paid_at=now if paid else None))
    s = client.get("/api/stats").get_json()
    assert s["revenue_today"] == 35
    assert s["revenue_week"] == 35
    assert s["revenue"][-1] == 35
    assert s["to_collect"] == 15


def test_money_survives_a_reload(client):
    login(client, "owner-pass")
    oid = add_order(client, price="9.9").get_json()["id"]
    client.post(f"/api/orders/{oid}/paid", json={"method": "alipay"})
    fileapp.store.load()                              # read everything back from the database
    order = fileapp.store.get(oid)
    assert (order.price, order.pay_method) == (9.9, "alipay") and order.paid_at
