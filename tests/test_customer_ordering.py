"""Customers order from the catalog; the server sets the price; the shop confirms."""
import fileapp
from conftest import login
from models import OrderStatus

PW = "Str0ng!Pass2026"


def owner_adds_products(client):
    login(client, "owner-pass")
    water = client.post("/api/products", json={"name": "桶装水", "price": "18", "unit": "桶"}).get_json()["product"]
    tea = client.post("/api/products", json={"name": "茶", "price": "4.5"}).get_json()["product"]
    hidden = client.post("/api/products", json={"name": "旧货", "price": "1"}).get_json()["product"]
    client.put(f"/api/products/{hidden['id']}", json={"active": False})
    client.post("/logout")
    return water, tea, hidden


def as_customer(client, phone="13900000001"):
    client.post("/signup", data={"role": "customer", "phone": phone, "name": "Kehu", "password": PW})
    client.post("/login", data={"phone": phone, "password": PW})


def place(client, items, **extra):
    body = {"items": items, "lat": 30.25, "lng": 119.72, "address": "5号楼302", **extra}
    return client.post("/api/me/order", json=body)


def test_customer_sees_only_visible_products(client):
    water, tea, hidden = owner_adds_products(client)
    as_customer(client)
    names = {p["name"] for p in client.get("/api/products?all=1").get_json()}
    assert names == {"桶装水", "茶"}


def test_server_computes_the_total(client):
    water, tea, _ = owner_adds_products(client)
    as_customer(client)
    r = place(client, [{"id": water["id"], "qty": 2}, {"id": tea["id"], "qty": 3, "price": 0}])
    d = r.get_json()
    assert d["ok"] and d["total"] == 49.5                 # 2*18 + 3*4.5, the browser price is ignored
    order = fileapp.store.get(d["id"])
    assert order.status == OrderStatus.REQUESTED and order.price == 49.5
    assert order.phone == "13900000001" and "5号楼302" in order.note
    mine = client.get("/api/me/orders").get_json()["orders"]
    assert [o["id"] for o in mine] == [d["id"]] and mine[0]["status"] == "requested"


def test_bad_orders_are_rejected(client):
    water, _, hidden = owner_adds_products(client)
    as_customer(client)
    assert place(client, []).status_code == 400                                   # nothing chosen
    assert place(client, [{"id": hidden["id"], "qty": 1}]).status_code == 400     # hidden product
    assert place(client, [{"id": "nope", "qty": 1}]).status_code == 400           # unknown product
    for q in (0, 100, "x", 1.5e9):
        assert place(client, [{"id": water["id"], "qty": q}]).status_code == 400, q
    assert place(client, [{"id": water["id"], "qty": 1}], address="").status_code == 400
    assert place(client, [{"id": water["id"], "qty": 1}], lat="nan").status_code == 400
    assert place(client, [{"id": water["id"], "qty": 1}], address="x" * 121).status_code == 400


def test_requested_orders_stay_out_of_the_route(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    oid = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    client.post("/logout")
    login(client, "owner-pass")
    route = client.get("/api/route").get_json()
    assert oid not in [s["id"] for s in route["stops"]]
    assert route["summary"]["requested_count"] == 1


def test_owner_confirms_and_the_order_joins_the_route(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    oid = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    client.post("/logout")
    login(client, "owner-pass")
    assert client.post(f"/api/orders/{oid}/confirm").get_json()["ok"]
    assert fileapp.store.get(oid).status == OrderStatus.PENDING
    assert oid in [s["id"] for s in client.get("/api/route").get_json()["stops"]]
    assert client.post(f"/api/orders/{oid}/confirm").status_code == 400          # not twice


def test_customer_cannot_confirm(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    oid = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    assert client.post(f"/api/orders/{oid}/confirm").status_code == 403


def test_customer_cancels_only_while_waiting(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    oid = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    assert client.post(f"/api/me/orders/{oid}/cancel").get_json()["ok"]
    assert fileapp.store.get(oid).status == OrderStatus.CANCELLED
    oid2 = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    fileapp.store.get(oid2).status = OrderStatus.PENDING                         # shop confirmed it
    assert client.post(f"/api/me/orders/{oid2}/cancel").status_code == 400


def test_customer_cannot_cancel_someone_elses_order(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client, phone="13900000001")
    oid = place(client, [{"id": water["id"], "qty": 1}]).get_json()["id"]
    client.post("/logout")
    as_customer(client, phone="13900000002")
    assert client.post(f"/api/me/orders/{oid}/cancel").status_code == 404
    assert fileapp.store.get(oid).status == OrderStatus.REQUESTED


def test_anti_spam_limits(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    for _ in range(fileapp.MAX_OPEN_REQUESTS):
        assert place(client, [{"id": water["id"], "qty": 1}]).get_json()["ok"]
    assert place(client, [{"id": water["id"], "qty": 1}]).status_code == 400     # too many waiting


def test_tracking_page_says_waiting(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    d = place(client, [{"id": water["id"], "qty": 1}]).get_json()
    t = client.get("/api/track/" + d["track_url"].split("/track/")[1].replace("?k=", "?k=")).get_json()
    assert t["status"] == "requested"


def test_items_survive_a_reload(client):
    water, _, _ = owner_adds_products(client)
    as_customer(client)
    oid = place(client, [{"id": water["id"], "qty": 2}]).get_json()["id"]
    fileapp.store.load()
    import json
    items = json.loads(fileapp.store.get(oid).items)
    assert items == [{"id": water["id"], "name": "桶装水", "unit": "桶", "price": 18, "qty": 2}]
