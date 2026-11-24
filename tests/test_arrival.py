"""Smart arrival: the tracking page says the driver is here only when it is true."""
import time

import db
import fileapp
from models import Order


def setup_driver(lat, lng, located_ago=0):
    u = fileapp.users.create("13900139011", "Arrive Driver", "Str0ng!Pass2026")
    fileapp.users.set_status(u["id"], "approved")
    fileapp.users.set_location(u["id"], lat, lng)
    if located_ago:
        con = db.connect(fileapp.users.path)
        con.execute("UPDATE users SET located_at = ? WHERE id = ?", (time.time() - located_ago, u["id"]))
        con.commit()
        con.close()
    return u["id"]


def track(client, order):
    return client.get(f"/api/track/{order.id}?k={fileapp.track_key(order.id)}").get_json()


def test_driver_at_the_door_means_arrived(client):
    uid = setup_driver(30.25000, 119.72000)
    o = fileapp.store.add(Order(customer_name="Li", lat=30.25030, lng=119.72010, driver_id=uid))   # ~35 m
    assert track(client, o)["arrived"] is True


def test_driver_far_away_is_not_arrived(client):
    uid = setup_driver(30.25000, 119.72000)
    o = fileapp.store.add(Order(customer_name="Li", lat=30.26000, lng=119.72000, driver_id=uid))   # ~1.1 km
    assert track(client, o)["arrived"] is False


def test_old_gps_proves_nothing(client):
    uid = setup_driver(30.25000, 119.72000, located_ago=600)
    o = fileapp.store.add(Order(customer_name="Li", lat=30.25010, lng=119.72000, driver_id=uid))
    assert track(client, o)["arrived"] is False
