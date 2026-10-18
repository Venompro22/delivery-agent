"""📊 Stats per driver use the same rules as the totals."""
import time

import fileapp
from conftest import login
from models import Order, OrderStatus


def delivered(driver_id, minutes, promise_in):
    now = time.time()
    return fileapp.store.add(Order(
        customer_name="X", lat=30.2, lng=120.1, driver_id=driver_id,
        status=OrderStatus.DELIVERED, created_at=now - minutes * 60, delivered_at=now,
        promised_by=now + promise_in * 60))


def test_stats_per_driver(client):
    login(client, "owner-pass")
    ali = fileapp.users.create("13800138000", "Ali", "blue-river-42")
    delivered(ali["id"], minutes=30, promise_in=-1)     # 1 minute late
    delivered(ali["id"], minutes=10, promise_in=10)     # on time
    rows = client.get("/api/stats").get_json()["drivers"]
    assert rows == [{"name": "Ali", "today": 2, "week": 2, "median_minutes": 20,
                     "on_time_pct": 50, "late": 1}]
