"""🚚 Drivers see their own orders + the pool; nobody touches someone else's order."""
import fileapp
from conftest import login, login_driver
from models import Order


def add(name, driver_id=None):
    return fileapp.store.add(Order(customer_name=name, lat=30.2, lng=120.1, driver_id=driver_id))


def stop_names(c):
    return {s["name"] for s in c.get("/api/route").get_json()["stops"]}


def my_id():
    return fileapp.users.by_phone("13900139000")["id"]


def test_driver_sees_own_orders_and_the_pool(client):
    login_driver(client)
    add("Mine", my_id())
    add("Pool")
    add("Someone else", "another-driver")
    assert stop_names(client) == {"Mine", "Pool"}


def test_driver_cannot_deliver_someone_elses_order(client):
    login_driver(client)
    o = add("Someone else", "another-driver")
    assert client.post(f"/api/orders/{o.id}/deliver").status_code == 404


def test_delivering_a_pool_order_claims_it(client):
    login_driver(client)
    o = add("Pool")
    assert client.post(f"/api/orders/{o.id}/deliver").status_code == 200
    assert fileapp.store.get(o.id).driver_id == my_id()
    assert client.post(f"/api/orders/{o.id}/deliver").status_code == 400   # already delivered


def test_owner_assigns_only_to_approved_drivers(client):
    login(client, "owner-pass")
    u = fileapp.users.create("13800138000", "Ali", "blue-river-42")
    o = add("X")
    r = client.post(f"/api/orders/{o.id}/assign", json={"driver_id": u["id"]})
    assert r.status_code == 400                       # Ali is still pending
    fileapp.users.set_status(u["id"], "approved")
    r = client.post(f"/api/orders/{o.id}/assign", json={"driver_id": u["id"]})
    assert r.status_code == 200
    assert fileapp.store.get(o.id).driver_id == u["id"]
