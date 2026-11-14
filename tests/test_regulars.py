"""🧠 The AI remembers regular customers: who they are, the usual place, the usual quantity."""
import fileapp
import customers
from models import Order


def _owner(client):
    with client.session_transaction() as s:
        s["auth"] = True; s["role"] = "owner"; s["v"] = fileapp.session_version()


def test_quantity_and_usual_place_words():
    assert customers.quantity("我要两桶水") == 2 and customers.quantity("3箱") == 3
    assert customers.quantity("你好") is None
    assert customers.wants_usual_place("老地方，送两桶") and not customers.wants_usual_place("文三路100号")


def test_regular_customer_is_recognised(client, monkeypatch):
    _owner(client)
    monkeypatch.setattr(fileapp, "predict_intent", lambda text: ("new_order", 0.95))
    for _ in range(3):
        fileapp.store.add(Order(customer_name="王芳", lat=30.25, lng=119.72,
                                phone="13800000001", note="2桶 文三路100号"))
    r = client.post("/api/agent", json={"text": "老地方 13800000001"}).get_json()["regular"]
    assert r["name"] == "王芳" and r["count"] == 3 and r["usual_place"]
    assert r["qty"] == 2 and r["lat"] == 30.25 and "文三路100号" in r["note"]


def test_new_customer_gets_no_memory(client, monkeypatch):
    _owner(client)
    monkeypatch.setattr(fileapp, "predict_intent", lambda text: ("new_order", 0.95))
    r = client.post("/api/agent", json={"text": "我要两桶水 13900000009"}).get_json()
    assert "regular" not in r
