"""Products: the owner manages the catalog; others can only read the visible part."""
import fileapp
from conftest import login, login_driver


def add(client, name="桶装水", price="18", unit="桶"):
    return client.post("/api/products", json={"name": name, "price": price, "unit": unit})


def test_owner_adds_and_lists_products(client):
    login(client, "owner-pass")
    r = add(client).get_json()
    assert r["ok"] and r["product"]["price"] == 18 and r["product"]["unit"] == "桶"
    names = [p["name"] for p in client.get("/api/products").get_json()]
    assert names == ["桶装水"]


def test_bad_products_are_rejected(client):
    login(client, "owner-pass")
    assert add(client, name="").status_code == 400
    assert add(client, name="x" * 61).status_code == 400
    for bad in ("abc", "-5", "nan", "inf", "1000000"):
        assert add(client, price=bad).status_code == 400, bad
    assert add(client, unit="u" * 21).status_code == 400


def test_edit_and_hide(client):
    login(client, "owner-pass")
    pid = add(client).get_json()["product"]["id"]
    r = client.put(f"/api/products/{pid}", json={"price": "20", "name": "农夫山泉"}).get_json()
    assert r["product"]["price"] == 20 and r["product"]["name"] == "农夫山泉"
    client.put(f"/api/products/{pid}", json={"active": False})
    assert client.get("/api/products").get_json() == []              # hidden from the normal list
    every = client.get("/api/products?all=1").get_json()
    assert len(every) == 1 and every[0]["active"] is False            # owner still sees it


def test_edit_unknown_product_is_404(client):
    login(client, "owner-pass")
    assert client.put("/api/products/nope", json={"price": "1"}).status_code == 404


def test_driver_reads_but_cannot_change(client):
    login(client, "owner-pass")
    pid = add(client).get_json()["product"]["id"]
    client.put(f"/api/products/{pid}", json={"active": False})
    add(client, name="矿泉水", price="2")
    client.post("/logout")
    login_driver(client)
    assert [p["name"] for p in client.get("/api/products?all=1").get_json()] == ["矿泉水"]
    assert add(client, name="hack").status_code in (401, 403)
    assert client.put(f"/api/products/{pid}", json={"active": True}).status_code in (401, 403)


def test_logged_out_cannot_read(client):
    assert client.get("/api/products").status_code in (302, 401)


def test_products_page_is_owner_only(client):
    login(client, "owner-pass")
    assert client.get("/products").status_code == 200
