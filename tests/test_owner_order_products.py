"""Owner's manual order form with products: items saved like customer orders, priced on the
server from the active catalog plus the delivery fee, then counted in the stats."""
import json

import fileapp
from conftest import login, login_driver
from test_customer_ordering import as_customer, owner_adds_products, place

SPOT = {"name": "王芳", "lat": 30.25, "lng": 119.72}


def owner_with_products(client, fee=5):
    water, tea, hidden = owner_adds_products(client)          # logs out at the end
    login(client, "owner-pass")
    client.put("/api/settings/shop", json={"delivery_fee": fee})
    return water, tea, hidden


def new_order(client, **body):
    return client.post("/api/orders", json={**SPOT, **body})


def test_items_saved_in_the_customer_order_format(client):
    water, tea, _ = owner_with_products(client)
    r = new_order(client, items=[{"id": water["id"], "qty": 2}, {"id": tea["id"], "qty": 1}], note="5号楼")
    assert r.status_code == 200
    owner_items = json.loads(fileapp.store.get(r.get_json()["id"]).items)
    assert owner_items == [{"id": water["id"], "name": "桶装水", "unit": "桶", "price": 18.0, "qty": 2},
                           {"id": tea["id"], "name": "茶", "unit": "", "price": 4.5, "qty": 1}]
    assert fileapp.store.get(r.get_json()["id"]).note.startswith("5号楼 | 桶装水 x2")

    client.post("/logout")                                    # the same cart, ordered by a customer
    as_customer(client)
    cid = place(client, [{"id": water["id"], "qty": 2}, {"id": tea["id"], "qty": 1}]).get_json()["id"]
    assert json.loads(fileapp.store.get(cid).items) == owner_items


def test_server_computes_the_price(client):
    water, tea, _ = owner_with_products(client, fee=5)
    r = new_order(client, price="1", items=[{"id": water["id"], "qty": 2, "price": 0.01},
                                           {"id": tea["id"], "qty": 2}])
    assert fileapp.store.get(r.get_json()["id"]).price == 18 * 2 + 4.5 * 2 + 5   # browser price ignored
    client.put("/api/settings/shop", json={"free_over": 30})
    r = new_order(client, items=[{"id": water["id"], "qty": 2}])
    assert fileapp.store.get(r.get_json()["id"]).price == 36                   # free delivery over 30


def test_inactive_and_bad_items_are_rejected(client):
    water, _, hidden = owner_with_products(client)
    for items in ([{"id": hidden["id"], "qty": 1}], [{"id": "nope", "qty": 1}],
                  [{"id": water["id"], "qty": 0}], [{"id": water["id"], "qty": "two"}], "water"):
        assert new_order(client, items=items).status_code == 400, items
    assert fileapp.store.all() == []


def test_orders_without_products_keep_note_and_manual_price(client):
    login(client, "owner-pass")
    for extra in ({}, {"items": []}):
        r = new_order(client, note="Bldg 5", price="42", **extra)
        o = fileapp.store.get(r.get_json()["id"])
        assert o.price == 42 and o.note == "Bldg 5" and o.items == ""


def test_delivered_order_shows_in_top_products(client):
    water, _, _ = owner_with_products(client)
    oid = new_order(client, items=[{"id": water["id"], "qty": 3}]).get_json()["id"]
    assert client.post(f"/api/orders/{oid}/deliver").status_code == 200
    top = client.get("/api/stats").get_json()["top_products"]
    assert top[0]["name"] == "桶装水" and top[0]["qty"] == 3


def test_drivers_cannot_and_viewers_stay_in_the_sandbox(client):
    water, _, _ = owner_with_products(client)
    client.post("/logout")
    login_driver(client)
    assert new_order(client, items=[{"id": water["id"], "qty": 1}]).status_code == 403
    client.post("/logout")
    client.get("/demo?k=" + fileapp.demo_key())
    assert client.get("/api/products").status_code == 200
    o = fileapp.store.get(new_order(client, items=[{"id": water["id"], "qty": 1}]).get_json()["id"])
    assert o.note.startswith(fileapp.DEMO_TAG) and o.driver_id is None and o.price == 23


def test_dashboard_has_the_picker_and_chinese_labels(client):
    login(client, "owner-pass")
    page = client.get("/").get_data(as_text=True)
    assert 'id="pick-search"' in page and "items: pickedItems()" in page
    i18n = client.get("/static/i18n.js").get_data(as_text=True)
    assert '"Search products": "搜索商品"' in i18n and '"Total ¥": "合计 ¥"' in i18n
