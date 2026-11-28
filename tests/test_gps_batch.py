"""📍 GPS step 1: the phone sends batches [{lat, lng, accuracy, ts}] from its offline queue.
Every point is checked; the newest good one is the driver position, all go to the trail."""
import time

import fileapp
import pytest
from conftest import login_driver

PHONE = "13900139000"


@pytest.fixture(autouse=True)
def fresh_trails(monkeypatch):
    monkeypatch.setattr(fileapp, "_TRAILS", {})


def ms(seconds_ago=0):
    return int((time.time() - seconds_ago) * 1000)


def send(client, points):
    return client.post("/api/driver/location", json={"points": points})


def me():
    return fileapp.users.by_phone(PHONE)


def test_batch_sets_the_newest_point_and_fills_the_trail(client):
    login_driver(client)
    pts = [{"lat": 30.2500, "lng": 119.72, "accuracy": 8, "ts": ms(120)},
           {"lat": 30.2530, "lng": 119.72, "accuracy": 6, "ts": ms(5)},       # newest, sent out of order
           {"lat": 30.2515, "lng": 119.72, "accuracy": 9, "ts": ms(60)}]
    r = send(client, pts)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "accepted": 3, "rejected": 0}
    assert (me()["lat"], me()["lng"]) == (30.2530, 119.72)
    assert abs(me()["located_at"] - (time.time() - 5)) < 2            # the real fix time, not "now"
    trail = list(fileapp._TRAILS[me()["id"]])
    assert [p[1] for p in trail] == [30.2500, 30.2515, 30.2530]       # in time order
    assert trail[0][0] == pytest.approx(time.time() - 120, abs=2)


def test_bare_list_is_accepted(client):
    login_driver(client)
    assert client.post("/api/driver/location", json=[{"lat": 30.26, "lng": 119.73, "ts": ms()}]).status_code == 200
    assert me()["lat"] == 30.26


def test_bad_points_are_dropped_and_counted(client):
    login_driver(client)
    good = {"lat": 30.25, "lng": 119.72, "accuracy": 5, "ts": ms(10)}
    bad = [{"lat": 91, "lng": 119.72, "ts": ms()},                       # out of range
           {"lat": 30.25, "lng": 181, "ts": ms()},
           {"lat": "nan", "lng": 119.72, "ts": ms()},
           {"lat": 30.25, "lng": 119.72, "ts": ms(-120)},                # 2 min in the future
           {"lat": 30.25, "lng": 119.72, "ts": ms(11 * 60)},             # older than 10 min
           {"lat": 30.25, "lng": 119.72},                                # no timestamp
           {"lat": 30.25, "lng": 119.72, "ts": ms(), "accuracy": -1},
           "not a point"]
    r = send(client, [good] + bad)
    assert r.status_code == 200 and r.get_json()["accepted"] == 1 and r.get_json()["rejected"] == len(bad)
    assert len(fileapp._TRAILS[me()["id"]]) == 1


def test_bad_batches_are_refused(client):
    login_driver(client)
    before = me()["lat"]
    one = {"lat": 30.25, "lng": 119.72, "ts": ms()}
    for points in ([], [one] * 101, "nope", {"lat": 1}):
        assert send(client, points).status_code == 400, points
    assert send(client, [{"lat": 30.25, "lng": 119.72, "ts": ms(3600)}]).status_code == 400   # nothing valid
    assert me()["lat"] == before


def test_an_old_batch_never_overwrites_a_newer_fix(client):
    login_driver(client)
    send(client, [{"lat": 30.30, "lng": 119.72, "ts": ms(5)}])
    send(client, [{"lat": 30.20, "lng": 119.72, "ts": ms(300)}])        # late offline batch
    assert me()["lat"] == 30.30


def test_old_single_point_still_works(client):
    login_driver(client)
    r = client.post("/api/driver/location", json={"lat": 30.25, "lng": 120.16})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert (me()["lat"], me()["lng"]) == (30.25, 120.16)
    assert abs(me()["located_at"] - time.time()) < 2
    assert client.post("/api/driver/location", json={"lat": "x", "lng": 1}).status_code == 400


def test_demo_viewer_rules_unchanged(client):
    client.get("/demo?k=" + fileapp.demo_key())
    default = (fileapp.driver.lat, fileapp.driver.lng)
    r = send(client, [{"lat": 30.26, "lng": 119.73, "ts": ms()}])
    assert r.status_code == 200
    with client.session_transaction() as s:
        assert s["here"] == [30.26, 119.73] and s["role"] == "viewer"
    assert (fileapp.driver.lat, fileapp.driver.lng) == default         # a viewer never moves the shop
    assert fileapp._TRAILS == {}                                        # nor writes a trail
    assert client.post("/api/drivers/x/status", json={}).status_code == 403   # other writes still blocked


def test_driver_page_loads_the_gps_module(client):
    login_driver(client)
    page = client.get("/driver").get_data(as_text=True)
    assert "/static/driver-gps.js" in page and 'id="gps-health"' in page and 'id="gps-denied"' in page
    js = client.get("/static/driver-gps.js").get_data(as_text=True)
    assert "watchPosition" in page and "Kalman" in js
