"""⭐ Customers rate a delivered order; the owner sees each driver's average."""
import pytest

import fileapp
from models import Order, OrderStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client with fresh temp stores, so the real db is never touched."""
    from store import OrderStore
    from users import UserStore
    monkeypatch.setattr(fileapp, "store", OrderStore(str(tmp_path / "o.db")))
    monkeypatch.setattr(fileapp, "users", UserStore(str(tmp_path / "u.db")))
    fileapp.app.config["TESTING"] = True
    return fileapp.app.test_client()


def _delivered_order(driver_id="d1"):
    o = Order(customer_name="Cust", lat=30.0, lng=120.0, driver_id=driver_id)
    o.status = OrderStatus.DELIVERED
    return o


def test_customer_can_rate_delivered_order(client):
    o = _delivered_order()
    fileapp.store.orders.append(o)
    key = fileapp.track_key(o.id)
    r = client.post(f"/api/track/{o.id}/rate?k={key}", json={"rating": 5})
    assert r.get_json() == {"ok": True, "rating": 5}
    assert fileapp.store.get(o.id).rating == 5


def test_wrong_key_is_rejected(client):
    o = _delivered_order()
    fileapp.store.orders.append(o)
    r = client.post(f"/api/track/{o.id}/rate?k=WRONG", json={"rating": 5})
    assert r.status_code == 404
    assert fileapp.store.get(o.id).rating is None


def test_cannot_rate_undelivered_order(client):
    o = Order(customer_name="Cust", lat=30.0, lng=120.0, driver_id="d1")  # still pending
    fileapp.store.orders.append(o)
    key = fileapp.track_key(o.id)
    r = client.post(f"/api/track/{o.id}/rate?k={key}", json={"rating": 5})
    assert r.status_code == 400


def test_cannot_rate_twice(client):
    o = _delivered_order()
    o.rating = 3
    fileapp.store.orders.append(o)
    key = fileapp.track_key(o.id)
    r = client.post(f"/api/track/{o.id}/rate?k={key}", json={"rating": 5})
    assert r.status_code == 400
    assert fileapp.store.get(o.id).rating == 3        # unchanged


def test_rating_out_of_range_is_rejected(client):
    o = _delivered_order()
    fileapp.store.orders.append(o)
    key = fileapp.track_key(o.id)
    for bad in (0, 6, -1):
        r = client.post(f"/api/track/{o.id}/rate?k={key}", json={"rating": bad})
        assert r.status_code == 400
