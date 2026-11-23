"""GPS: every browser has its own start point, and the demo follows a visitor to their city."""
import fileapp
from conftest import login
from geo import haversine_km
from models import OrderStatus

CASABLANCA = (33.5731, -7.5898)


def live_demo():
    return [o for o in fileapp.store.all()
            if (o.note or "").startswith(fileapp.DEMO_TAG) and o.status == OrderStatus.PENDING]


def test_demo_visitor_gps_moves_the_demo_to_their_city(client):
    client.get("/try")
    assert live_demo() and all(haversine_km(o.lat, o.lng, 30.257, 119.727) < 10 for o in live_demo())
    r = client.post("/api/driver/location", json={"lat": CASABLANCA[0], "lng": CASABLANCA[1]})
    assert r.get_json()["ok"]
    assert live_demo() and all(haversine_km(o.lat, o.lng, *CASABLANCA) < 10 for o in live_demo())
    start = client.get("/api/route").get_json()["driver"]
    assert (start["lat"], start["lng"]) == CASABLANCA


def test_visitor_gps_does_not_move_the_shared_start_point(client):
    before = (fileapp.driver.lat, fileapp.driver.lng)
    client.get("/try")
    client.post("/api/driver/location", json={"lat": CASABLANCA[0], "lng": CASABLANCA[1]})
    assert (fileapp.driver.lat, fileapp.driver.lng) == before


def test_small_moves_keep_the_same_demo_orders(client):
    client.get("/try")
    client.post("/api/driver/location", json={"lat": CASABLANCA[0], "lng": CASABLANCA[1]})
    ids = sorted(o.id for o in live_demo())
    client.post("/api/driver/location", json={"lat": CASABLANCA[0] + 0.01, "lng": CASABLANCA[1]})
    assert sorted(o.id for o in live_demo()) == ids          # ~1 km: nothing rebuilt


def test_owner_location_is_remembered_by_the_session(client):
    login(client, "owner-pass")
    client.post("/api/driver/location", json={"lat": 31.63, "lng": -8.0})
    fileapp.driver.lat, fileapp.driver.lng = 30.257, 119.727    # e.g. the server restarted
    start = client.get("/api/route").get_json()["driver"]
    assert (start["lat"], start["lng"]) == (31.63, -8.0)
