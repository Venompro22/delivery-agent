"""Owner AI, step 3: a customer types an order, the AI fills the cart, nothing is sent by itself."""
import fileapp
from order_parser import match_products
from test_customer_ordering import as_customer, owner_adds_products, place

CATALOG = [{"id": "a", "name": "桶装水", "unit": "桶"}, {"id": "b", "name": "矿泉水", "unit": "箱"},
           {"id": "c", "name": "Sparkling water", "unit": "bottle"}]


def test_match_products():
    r = match_products("两桶水送到5号楼302", CATALOG)
    assert r["items"] == [{"id": "a", "qty": 2}] and r["address"] == "5号楼302"
    r = match_products("一箱矿泉水和3桶水 阳光小区3单元", CATALOG)
    assert r["items"] == [{"id": "b", "qty": 1}, {"id": "a", "qty": 3}]
    assert match_products("sparkling water x3", CATALOG)["items"] == [{"id": "c", "qty": 3}]
    assert match_products("2 sparkling water", CATALOG)["items"] == [{"id": "c", "qty": 2}]
    r = match_products("我要香蕉", CATALOG)
    assert r["items"] == [] and r["unmatched"] == ["我要香蕉"]


def test_ai_order_fills_the_cart_without_ordering(client):
    water, tea, hidden = owner_adds_products(client)
    as_customer(client)
    d = client.post("/api/me/ai-order", json={"text": "两桶水送到5号楼302"}).get_json()
    assert d["ok"] and d["address"] == "5号楼302"
    assert d["items"] == [{"id": water["id"], "name": "桶装水", "unit": "桶", "price": 18.0, "qty": 2}]
    assert fileapp.store.all() == []                          # nothing ordered yet
    # The customer then sends the cart the normal way; the server prices it
    assert place(client, [{"id": i["id"], "qty": i["qty"]} for i in d["items"]]).get_json()["total"] == 36


def test_hidden_products_are_never_matched(client):
    owner_adds_products(client)
    as_customer(client)
    assert client.post("/api/me/ai-order", json={"text": "一个旧货"}).get_json()["items"] == []


def test_ai_order_is_for_customers_only(client):
    from conftest import login
    login(client, "owner-pass")
    assert client.post("/api/me/ai-order", json={"text": "两桶水"}).status_code == 403
    client.post("/logout")
    as_customer(client)
    assert client.post("/api/me/ai-order", json={"text": ""}).status_code == 400
    assert "ai-text" in client.get("/me").get_data(as_text=True)
