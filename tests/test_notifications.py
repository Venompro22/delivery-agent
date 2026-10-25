"""🔔 Drivers get a 'new orders' badge until they look at their list."""
import time

from models import Order, OrderStatus
from store import OrderStore
from users import UserStore


def _setup(tmp_path):
    """One approved driver + an empty order store, both on temp dbs."""
    u = UserStore(str(tmp_path / "u.db"))
    s = OrderStore(str(tmp_path / "o.db"))
    d = u.create("13800000001", "Tester", "GoodPass2026!")
    u.set_status(d["id"], "approved")
    return u, s, d


def _new_count(u, s, uid):
    """Same logic as /api/route: my pending orders newer than seen_at."""
    seen = u.get_seen_at(uid)
    return sum(1 for o in s.all()
               if o.driver_id == uid
               and (o.created_at or 0) > seen
               and o.status == OrderStatus.PENDING)


def test_new_driver_has_never_seen(tmp_path):
    u, s, d = _setup(tmp_path)
    assert u.get_seen_at(d["id"]) == 0.0


def test_assigned_order_counts_as_new(tmp_path):
    u, s, d = _setup(tmp_path)
    s.add(Order(customer_name="A", lat=30.0, lng=120.0, driver_id=d["id"]))
    assert _new_count(u, s, d["id"]) == 1


def test_mark_seen_clears_the_badge(tmp_path):
    u, s, d = _setup(tmp_path)
    s.add(Order(customer_name="A", lat=30.0, lng=120.0, driver_id=d["id"]))
    u.mark_seen(d["id"])
    assert _new_count(u, s, d["id"]) == 0


def test_old_orders_are_not_new(tmp_path):
    u, s, d = _setup(tmp_path)
    u.mark_seen(d["id"])                                  # driver looks now
    time.sleep(0.01)
    # an order created BEFORE the driver looked (older timestamp)
    old = Order(customer_name="Old", lat=30.0, lng=120.0,
                driver_id=d["id"], created_at=time.time() - 9999)
    s.add(old)
    assert _new_count(u, s, d["id"]) == 0


def test_delivered_orders_dont_count(tmp_path):
    u, s, d = _setup(tmp_path)
    o = Order(customer_name="A", lat=30.0, lng=120.0, driver_id=d["id"])
    o.status = OrderStatus.DELIVERED
    s.add(o)
    assert _new_count(u, s, d["id"]) == 0
