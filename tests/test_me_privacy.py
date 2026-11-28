"""🛡️ GPS step 3: each customer sees only their own order, its own driver once they are next,
the trail driven FOR their order, and the trail saved with their own delivered orders."""
import json
import time

import fileapp
import pytest
from conftest import login, login_driver
from models import Order, OrderStatus

PW = "Str0ng!Pass2026"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(fileapp, "_road_line", lambda a, b: [list(a), list(b)])
    monkeypatch.setattr(fileapp, "_TRAILS", {})
    monkeypatch.setattr(fileapp, "_NOT_NEXT_AT", {})


def ms(seconds_ago):
    return int((time.time() - seconds_ago) * 1000)


def driver_phone():
    phone = fileapp.app.test_client()
    login_driver(phone)
    return phone, fileapp.users.by_phone("13900139000")["id"]


def drive(phone, points):
    """points: [(lat, seconds_ago)] along lng 119.72, sent as one batch with real timestamps."""
    r = phone.post("/api/driver/location",
                   json={"points": [{"lat": lat, "lng": 119.72, "accuracy": 5, "ts": ms(ago)} for lat, ago in points]})
    assert r.status_code == 200, r.get_json()


def customer(phone_no, order):
    c = fileapp.app.test_client()
    c.post("/signup", data={"role": "customer", "phone": phone_no, "name": "Kehu", "password": PW})
    c.post("/login", data={"phone": phone_no, "password": PW})
    fileapp.users.link_order(fileapp.users.by_phone(phone_no)["id"], order.id)
    return c


def two_customers(uid):
    """A's order is the driver's next stop (closer); B's comes after it."""
    a = fileapp.store.add(Order(customer_name="Alice", lat=30.2500, lng=119.72, driver_id=uid, note="A street 1"))
    b = fileapp.store.add(Order(customer_name="Bob", lat=30.2900, lng=119.72, driver_id=uid, note="B street 2"))
    for o in (a, b):
        o.created_at -= 3600
    return a, b, customer("13900000031", a), customer("13900000032", b)


def live(c):
    return c.get("/api/me/live").get_json()["orders"][0]


def test_no_driver_position_before_they_are_next(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    drive(phone, [(30.2400, 20), (30.2410, 5)])
    ob = live(cb)
    assert ob["next"] is False and ob["stops_before"] == 1
    assert ob["driver"] is None and ob["driver_age_s"] is None and ob["road"] == [] and ob["trail"] == []
    body = cb.get("/api/me/live").get_data(as_text=True)
    assert "30.241" not in body and "Alice" not in body and "A street" not in body and a.id not in body
    oa = live(ca)                                               # A is next: sees the driver
    assert oa["next"] is True and oa["driver"] == [30.241, 119.72] and 0 <= oa["driver_age_s"] < 60


def test_trail_starts_when_the_previous_delivery_is_done(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    drive(phone, [(30.2400, 300), (30.2450, 250), (30.2500, 200)])   # driving to Alice's door
    assert phone.post(f"/api/orders/{a.id}/deliver").status_code == 200
    a.delivered_at = time.time() - 150                                # delivered 150 s ago
    drive(phone, [(30.2510, 100), (30.2600, 60), (30.2700, 30)])     # then on to Bob
    ob = live(cb)
    assert ob["next"] is True
    lats = [p[0] for p in ob["trail"]]
    assert lats == [30.26, 30.27]                    # 30.251 hidden: right at Alice's door
    assert not any(lat <= 30.25 for lat in lats)     # nothing from before Bob was next


def test_driver_hidden_while_still_at_the_previous_door(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    drive(phone, [(30.2450, 60), (30.2500, 30)])             # at Alice's door
    phone.post(f"/api/orders/{a.id}/deliver")
    ob = live(cb)
    assert ob["next"] is True and ob["driver"] is None and ob["trail"] == []
    assert "30.25," not in cb.get("/api/me/live").get_data(as_text=True)
    drive(phone, [(30.2560, 0)])                             # ~670 m on toward Bob
    assert live(cb)["driver"] == [30.256, 119.72]


def test_seen_not_next_moves_the_trail_start(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    before = time.time()
    assert live(cb)["next"] is False                 # Bob looks while Alice is still first
    assert fileapp._trail_start(b) >= before
    fileapp.late_now([a, b], time.time())            # the minute GPS watch records it too
    assert fileapp._NOT_NEXT_AT[b.id] >= before and a.id not in fileapp._NOT_NEXT_AT


def test_pool_orders_never_show_a_driver(client):
    login(client, "owner-pass")
    client.post("/api/driver/location", json={"lat": 30.30, "lng": 119.80})    # the owner's own GPS
    client.post("/logout")
    o = fileapp.store.add(Order(customer_name="Pool", lat=30.25, lng=119.72))
    c = customer("13900000033", o)
    item = live(c)
    assert item["driver"] is None and item["trail"] == [] and item["road"] == []


def test_ids_from_other_customers_get_nothing(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    drive(phone, [(30.2400, 60), (30.2450, 30)])
    phone.post(f"/api/orders/{a.id}/deliver")
    assert fileapp.store.get(a.id).trail                     # Alice's order has a saved trail
    # Bob tries Alice's id everywhere
    assert cb.get(f"/api/me/orders/{a.id}/trail").status_code == 404
    assert cb.get(f"/api/me/orders/{b.id}/trail").status_code == 404     # his own, not delivered yet
    assert cb.get(f"/api/track/{a.id}").status_code == 404                # no tracking key
    assert cb.get(f"/api/track/{a.id}?k=" + fileapp.track_key(b.id)).status_code == 404
    assert cb.post("/api/me/claim", json={"link": f"/track/{a.id}?k=forged"}).status_code == 400
    for url in (f"/api/orders/{a.id}/proof", "/api/route", "/api/orders", "/api/stats", "/api/drivers"):
        assert cb.get(url).status_code == 403, url
    mine = cb.get("/api/me/orders").get_data(as_text=True) + cb.get("/api/me/live").get_data(as_text=True)
    assert a.id not in mine and "Alice" not in mine and "A street" not in mine


def test_saved_trail_only_in_their_own_history(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    # 350 points toward Alice: the saved trail is simplified to 300, first and last kept
    for start in (0, 100, 200, 300):
        n = min(100, 350 - start)
        drive(phone, [(30.2000 + (start + i) * 0.0001, 400 - start - i) for i in range(n)])
    phone.post(f"/api/orders/{a.id}/deliver")
    saved = json.loads(fileapp.store.get(a.id).trail)
    assert 2 <= len(saved) <= fileapp.TRAIL_SAVE_MAX
    view = [o for o in ca.get("/api/me/orders").get_json()["orders"] if o["id"] == a.id][0]
    assert view["has_trail"] is True and "trail" not in view            # the list never carries trails
    r = ca.get(f"/api/me/orders/{a.id}/trail").get_json()
    assert r["ok"] and r["trail"] == saved and r["trail"][-1] == [30.2349, 119.72]
    assert a.id not in cb.get("/api/me/orders").get_data(as_text=True)
    assert cb.get(f"/api/me/orders/{a.id}/trail").status_code == 404
    login(client, "owner-pass")
    assert client.get(f"/api/me/orders/{a.id}/trail").status_code == 403   # customers only
    assert phone.get(f"/api/me/orders/{a.id}/trail").status_code == 403


def test_owner_ai_delivery_saves_the_trail_too(client):
    phone, uid = driver_phone()
    a, b, ca, cb = two_customers(uid)
    drive(phone, [(30.2400, 60), (30.2450, 30)])
    fileapp._mark_delivered(fileapp.store.get(a.id))                   # what the AI action calls
    assert fileapp.store.get(a.id).status == OrderStatus.DELIVERED and fileapp.store.get(a.id).trail


def test_me_page_has_the_route_view_and_last_seen(client):
    o = fileapp.store.add(Order(customer_name="X", lat=30.25, lng=119.72))
    page = customer("13900000034", o).get("/me").get_data(as_text=True)
    assert "showRoute(" in page and "/trail" in page and "lastSeen" in page and "mp-car-arrow" in page
