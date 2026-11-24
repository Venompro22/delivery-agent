"""Plan B: without GPS, a demo visitor lands on their city from their IP."""
import fileapp
from geo import haversine_km, wgs84_to_gcj02
from models import OrderStatus

CASABLANCA = (33.5731, -7.5898, "Casablanca")


def live_demo():
    return [o for o in fileapp.store.all()
            if (o.note or "").startswith(fileapp.DEMO_TAG) and o.status == OrderStatus.PENDING]


def test_demo_visitor_goes_to_their_city(client, monkeypatch):
    monkeypatch.setattr(fileapp, "_ip_location", lambda ip: CASABLANCA)
    client.get("/try")
    d = client.get("/api/where-am-i").get_json()
    assert d["ok"] and d["city"] == "Casablanca"
    assert all(haversine_km(o.lat, o.lng, 33.5731, -7.5898) < 10 for o in live_demo())


def test_real_gps_wins_over_the_ip_guess(client, monkeypatch):
    monkeypatch.setattr(fileapp, "_ip_location", lambda ip: CASABLANCA)
    client.get("/try")
    client.post("/api/driver/location", json={"lat": 31.63, "lng": -8.0})
    assert client.get("/api/where-am-i").get_json()["ok"] is False


def test_owner_is_never_moved_by_ip(client, monkeypatch):
    from conftest import login
    monkeypatch.setattr(fileapp, "_ip_location", lambda ip: CASABLANCA)
    login(client, "owner-pass")
    assert client.get("/api/where-am-i").get_json()["ok"] is False


def test_private_ips_are_not_looked_up():
    assert fileapp._ip_location("127.0.0.1") is None
    assert fileapp._ip_location("192.168.1.5") is None
    assert fileapp._ip_location("not-an-ip") is None


def test_china_conversion_keeps_lat_lng_order():
    lat, lng = wgs84_to_gcj02(30.25, 119.72)
    assert haversine_km(lat, lng, 30.25, 119.72) < 1
