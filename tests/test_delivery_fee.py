"""Delivery fee, free delivery threshold and minimum order: the server decides."""
import fileapp
from conftest import login
from test_customer_ordering import owner_adds_products, as_customer, place


def set_rules(client, **rules):
    login(client, "owner-pass")
    r = client.put("/api/settings/shop", json=rules)
    client.post("/logout")
    return r


def test_defaults_are_zero(client):
    login(client, "owner-pass")
    assert client.get("/api/settings/shop").get_json() == {"delivery_fee": 0, "free_over": 0, "min_order": 0}


def test_bad_settings_are_rejected(client):
    login(client, "owner-pass")
    for bad in ({"delivery_fee": "abc"}, {"free_over": "-1"}, {"min_order": "nan"}, {"delivery_fee": "99999"}):
        assert client.put("/api/settings/shop", json=bad).status_code == 400, bad


def test_fee_is_added_until_the_free_threshold(client):
    water, _, _ = owner_adds_products(client)                  # water costs 18
    set_rules(client, delivery_fee="3", free_over="50")
    as_customer(client)
    small = place(client, [{"id": water["id"], "qty": 2}]).get_json()
    assert small["total"] == 39 and small["fee"] == 3           # 36 + 3
    big = place(client, [{"id": water["id"], "qty": 3}]).get_json()
    assert big["total"] == 54 and big["fee"] == 0               # 54 >= 50: free
    assert "delivery 3" in fileapp.store.get(small["id"]).note


def test_minimum_order(client):
    water, tea, _ = owner_adds_products(client)                 # tea costs 4.5
    set_rules(client, min_order="10")
    as_customer(client)
    assert place(client, [{"id": tea["id"], "qty": 1}]).status_code == 400
    assert place(client, [{"id": tea["id"], "qty": 3}]).get_json()["ok"]    # 13.5


def test_customer_reads_but_cannot_change(client):
    owner_adds_products(client)
    set_rules(client, delivery_fee="5")
    as_customer(client)
    assert client.get("/api/me/shop").get_json()["delivery_fee"] == 5
    assert client.put("/api/settings/shop", json={"delivery_fee": "0"}).status_code == 403


def test_settings_survive_a_new_store(client):
    set_rules(client, delivery_fee="2.5", min_order="8")
    fresh = fileapp.SettingsStore(fileapp.store.path)
    assert fresh.all()["delivery_fee"] == 2.5 and fresh.all()["min_order"] == 8
