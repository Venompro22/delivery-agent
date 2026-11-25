# Driver screen: their own day only, and demo drivers can collect money on demo orders.
import time

import fileapp
from conftest import login_driver
from models import Order, OrderStatus


def test_today_counts_only_my_deliveries_and_my_cash(client):
    login_driver(client)
    me = fileapp.users.by_phone("13900139000")["id"]
    now = time.time()
    fileapp.store.add(Order(customer_name="A", lat=30.2, lng=120.1, driver_id=me, price=20,
                            status=OrderStatus.DELIVERED, delivered_at=now, pay_method="cash", paid_at=now))
    fileapp.store.add(Order(customer_name="B", lat=30.2, lng=120.1, driver_id=me, price=15,
                            status=OrderStatus.DELIVERED, delivered_at=now))
    fileapp.store.add(Order(customer_name="C", lat=30.2, lng=120.1, driver_id="someone-else", price=99,
                            status=OrderStatus.DELIVERED, delivered_at=now, pay_method="cash", paid_at=now))
    d = client.get("/api/driver/today").get_json()
    assert d == {"ok": True, "delivered": 2, "cash": 20, "online": 0, "to_collect": 15}


def test_demo_driver_can_collect_on_demo_orders(client):
    client.get("/try/driver")
    stop = client.get("/api/route").get_json()["stops"][0]
    r = client.post(f"/api/orders/{stop['id']}/paid", json={"method": "cash"})
    assert r.get_json()["ok"]


def test_demo_driver_cannot_touch_real_orders(client):
    real = fileapp.store.add(Order(customer_name="Real", lat=30.2, lng=120.1))
    client.get("/try/driver")
    assert client.post(f"/api/orders/{real.id}/paid", json={"method": "cash"}).status_code == 403


def test_driver_page_is_the_new_screen_with_a_post_logout(client):
    login_driver(client)
    html = client.get("/driver").get_data(as_text=True)
    assert 'id="map"' in html and "driver-pro.css" in html and "/api/driver/today" in html
    assert 'action="/logout"' in html and 'method="post"' in html
