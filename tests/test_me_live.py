# Customer live view: the driver coming, the road only when they are next, nothing about others.
import fileapp
from conftest import login, login_driver  # noqa: F401
from models import Order

PW = "Str0ng!Pass2026"


def setup(client, monkeypatch, someone_first=False):
    monkeypatch.setattr(fileapp, "_road_line", lambda a, b: [list(a), list(b)])
    fileapp._TRAILS.clear()
    phone = fileapp.app.test_client()                   # the driver's phone
    login_driver(phone)
    uid = fileapp.users.by_phone("13900139000")["id"]
    if someone_first:
        fileapp.store.add(Order(customer_name="Other", lat=30.2470, lng=119.7200, driver_id=uid,
                                note="Secret street 9"))
    mine = fileapp.store.add(Order(customer_name="Me", lat=30.2600, lng=119.7200, driver_id=uid))
    phone.post("/api/driver/location", json={"lat": 30.2400, "lng": 119.7200})
    phone.post("/api/driver/location", json={"lat": 30.2450, "lng": 119.7200})
    client.post("/signup", data={"role": "customer", "phone": "13900000021", "name": "Kehu", "password": PW})
    client.post("/login", data={"phone": "13900000021", "password": PW})
    fileapp.users.link_order(fileapp.users.by_phone("13900000021")["id"], mine.id)
    return mine


def test_next_customer_sees_driver_road_and_trail(client, monkeypatch):
    setup(client, monkeypatch)
    o = client.get("/api/me/live").get_json()["orders"][0]
    assert o["next"] is True and o["driver"] == [30.245, 119.72]
    assert len(o["road"]) >= 2 and o["trail"]


def test_not_next_sees_no_road_and_nothing_about_others(client, monkeypatch):
    setup(client, monkeypatch, someone_first=True)
    r = client.get("/api/me/live")
    o = r.get_json()["orders"][0]
    assert o["next"] is False and o["stops_before"] == 1
    assert o["road"] == [] and o["trail"] == []
    body = r.get_data(as_text=True)
    assert "Other" not in body and "Secret" not in body and "30.247" not in body


def test_trail_hides_the_previous_door(client, monkeypatch):
    setup(client, monkeypatch)
    trail = client.get("/api/me/live").get_json()["orders"][0]["trail"]
    assert [30.24, 119.72] not in trail


def test_only_customers(client):
    login(client, "owner-pass")
    assert client.get("/api/me/live").status_code == 403


def test_new_screen_has_the_map(client, monkeypatch):
    setup(client, monkeypatch)
    html = client.get("/me").get_data(as_text=True)
    assert 'id="map"' in html and "me-pro.css" in html and "/api/me/live" in html
