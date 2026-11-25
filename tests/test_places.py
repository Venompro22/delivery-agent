"""Learned delivery spots: drivers teach the app where customers really are."""
import fileapp
from conftest import login, login_driver
from models import Order

PHONE = "13800000001"


def deliver_from(client, gps, pin=(30.2500, 119.7200), note=""):
    login_driver(client)
    uid = fileapp.users.by_phone("13900139000")["id"]
    client.post("/api/driver/location", json={"lat": gps[0], "lng": gps[1]})
    o = fileapp.store.add(Order(customer_name="Li", lat=pin[0], lng=pin[1], phone=PHONE,
                                driver_id=uid, note=note))
    assert client.post(f"/api/orders/{o.id}/deliver").get_json()["ok"]
    client.post("/logout")


def new_order(client, lat, lng):
    login(client, "owner-pass")
    d = client.post("/api/orders", json={"name": "Li", "lat": lat, "lng": lng, "phone": PHONE}).get_json()
    return d, fileapp.store.get(d["id"])


def test_delivery_teaches_the_real_spot(client):
    deliver_from(client, gps=(30.2510, 119.7200))                     # ~110 m from the pin
    spot = fileapp.place_store().get(PHONE)
    assert spot and abs(spot[0] - 30.2510) < 1e-6


def test_nearby_pin_snaps_onto_the_learned_spot(client):
    deliver_from(client, gps=(30.2510, 119.7200))
    d, o = new_order(client, 30.2520, 119.7200)                       # sloppy pin ~110 m off
    assert d["snapped"] is True and abs(o.lat - 30.2510) < 1e-6


def test_far_pin_is_a_different_address(client):
    deliver_from(client, gps=(30.2510, 119.7200))
    d, o = new_order(client, 30.2700, 119.7200)                       # ~2 km: keep it
    assert d["snapped"] is False and o.lat == 30.27


def test_gps_far_from_the_pin_teaches_nothing(client):
    deliver_from(client, gps=(30.2600, 119.7200))                     # ~1.1 km off the pin
    assert fileapp.place_store().get(PHONE) is None


def test_demo_orders_teach_nothing(client):
    deliver_from(client, gps=(30.2510, 119.7200), note=fileapp.DEMO_TAG + "x")
    assert fileapp.place_store().get(PHONE) is None


def test_average_moves_slowly():
    import tempfile, os
    from places import PlaceStore
    ps = PlaceStore(os.path.join(tempfile.mkdtemp(), "p.db"))
    ps.learn(PHONE, 30.0, 120.0)
    lat, _, n = ps.learn(PHONE, 30.01, 120.0)
    assert n == 2 and abs(lat - 30.005) < 1e-9
