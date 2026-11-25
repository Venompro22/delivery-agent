"""GPS watch: one Telegram alert per real problem, never spam."""
import time

import db
import fileapp
from models import Order


def fresh_watch(monkeypatch):
    monkeypatch.setattr(fileapp, "_watch", {"still": {}, "sent": set()})


def driver_at(lat, lng, at):
    u = fileapp.users.by_phone("13900139022") or fileapp.users.create("13900139022", "Ali", "Str0ng!Pass2026")
    fileapp.users.set_status(u["id"], "approved")
    fileapp.users.set_location(u["id"], lat, lng)
    con = db.connect(fileapp.users.path)
    con.execute("UPDATE users SET located_at = ? WHERE id = ?", (at, u["id"]))
    con.commit()
    con.close()
    return u["id"]


def give_order(uid, lat=30.30, lng=119.80, **kw):
    return fileapp.store.add(Order(customer_name="Li", lat=lat, lng=lng, driver_id=uid, **kw))


def test_gps_lost_alerts_once(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    uid = driver_at(30.25, 119.72, now - 600)
    give_order(uid)
    assert any("GPS lost" in m for m in fileapp.gps_watch(now))
    assert not any("GPS lost" in m for m in fileapp.gps_watch(now + 60))       # no spam


def test_stopped_driver_alerts(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    uid = driver_at(30.25, 119.72, now)
    give_order(uid)
    assert fileapp.gps_watch(now) == []
    driver_at(30.25, 119.72, now + 700)                                         # same spot, fresh GPS
    assert any("stopped" in m for m in fileapp.gps_watch(now + 700))


def test_moving_driver_is_fine(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    uid = driver_at(30.25, 119.72, now)
    give_order(uid)
    fileapp.gps_watch(now)
    driver_at(30.252, 119.72, now + 700)                                        # ~220 m further
    assert fileapp.gps_watch(now + 700) == []


def test_waiting_at_a_customer_door_is_fine(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    uid = driver_at(30.25, 119.72, now)
    give_order(uid, lat=30.2505, lng=119.72)                                    # ~55 m away
    fileapp.gps_watch(now)
    driver_at(30.25, 119.72, now + 700)
    assert not any("stopped" in m for m in fileapp.gps_watch(now + 700))


def test_late_order_alerts_once(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    uid = driver_at(30.25, 119.72, now)
    give_order(uid, promised_by=now - 60)
    assert any("Running late" in m for m in fileapp.gps_watch(now))
    assert not any("Running late" in m for m in fileapp.gps_watch(now + 60))


def test_driver_without_orders_is_not_watched(client, monkeypatch):
    fresh_watch(monkeypatch)
    now = time.time()
    driver_at(30.25, 119.72, now - 600)
    assert fileapp.gps_watch(now) == []
