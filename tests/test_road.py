"""🛣️ Road routing: correct China coordinates, and a straight line when OSRM is down."""
import fileapp
from geo import wgs84_to_gcj02, gcj02_to_wgs84


def _owner(client):
    with client.session_transaction() as s:
        s["auth"] = True; s["role"] = "owner"; s["v"] = fileapp.session_version()


def test_gcj_wgs_round_trip():
    lat, lng = gcj02_to_wgs84(*wgs84_to_gcj02(30.257, 119.727))
    assert abs(lat - 30.257) < 1e-5 and abs(lng - 119.727) < 1e-5


def test_road_falls_back_to_a_straight_line(client, monkeypatch):
    _owner(client)
    def down(*a, **k):
        raise OSError("OSRM down")
    monkeypatch.setattr(fileapp.requests, "get", down)
    d = client.get("/api/road?pts=30.25,119.72;30.26,119.73").get_json()
    assert d["ok"] and d["road"] is False and len(d["path"]) == 2


def test_road_rejects_bad_points(client):
    _owner(client)
    assert client.get("/api/road?pts=abc").status_code == 400
    assert client.get("/api/road?pts=30.25,119.72").status_code == 400


def test_route_gives_raw_times_for_the_risk_card(client):
    import time
    from models import Order
    _owner(client)
    fileapp.store.add(Order(customer_name="R", lat=30.25, lng=119.72, promised_by=time.time() + 600))
    stop = client.get("/api/route").get_json()["stops"][0]
    assert stop["eta_epoch"] and stop["promised_epoch"]


def test_csp_allows_live_traffic_tiles(client):
    assert "https://tm.amap.com" in client.get("/login").headers["Content-Security-Policy"]
