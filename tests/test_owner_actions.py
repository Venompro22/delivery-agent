"""Owner AI, step 2: commands return a preview card; nothing changes until Confirm."""
import fileapp
from conftest import login, login_driver
from models import Order, OrderStatus


def order(name="李明", phone="13912345678", price=None, status=OrderStatus.PENDING):
    o = Order(customer_name=name, lat=30.25, lng=119.72, phone=phone, price=price, status=status)
    fileapp.store.add(o)
    return o


def preview(client, text):
    r = client.post("/api/agent", json={"text": text}).get_json()
    assert r["action"] == "preview", r
    return r


def confirm(client, token):
    return client.post("/api/agent/confirm", json={"token": token})


def test_paid_preview_changes_nothing_until_confirm(client):
    login(client, "owner-pass")
    o = order()
    r = preview(client, "李明 已付款 微信")
    assert r["kind"] == "paid" and "李明" in r["summary"] and "wechat" in r["summary"]
    assert fileapp.store.get(o.id).paid_at is None              # preview only
    res = confirm(client, r["token"])
    assert res.status_code == 200 and "✅" in res.get_json()["reply"]
    assert fileapp.store.get(o.id).pay_method == "wechat"
    assert confirm(client, r["token"]).status_code == 400       # a token works once


def test_assign_deliver_cancel_price(client):
    login(client, "owner-pass")
    d = fileapp.users.create("13900139001", "Ahmed", "driver-pass-42")
    fileapp.users.set_status(d["id"], "approved")
    a, b = order("王芳", "15800002222", price=30), order("Karim", "13700001111")

    r = preview(client, "assign Karim to Ahmed")
    assert "Ahmed" in r["summary"] and fileapp.store.get(b.id).driver_id is None
    confirm(client, r["token"])
    assert fileapp.store.get(b.id).driver_id == d["id"]

    r = preview(client, "王芳 价格 35.5")
    assert "30" in r["summary"] and "35.5" in r["summary"]
    confirm(client, r["token"])
    assert fileapp.store.get(a.id).price == 35.5

    r = preview(client, "mark Karim delivered")
    confirm(client, r["token"])
    assert fileapp.store.get(b.id).status == OrderStatus.DELIVERED

    r = preview(client, "lghi talab dyal 王芳")
    assert r["lang"] == "darija"
    confirm(client, r["token"])
    assert fileapp.store.get(a.id).status == OrderStatus.CANCELLED


def test_add_product_and_delivery_fee(client):
    login(client, "owner-pass")
    r = preview(client, "添加产品 矿泉水 2元/瓶")
    assert fileapp.product_store().all() == []
    confirm(client, r["token"])
    p = fileapp.product_store().all()[0]
    assert (p["name"], p["price"], p["unit"]) == ("矿泉水", 2.0, "瓶")

    r = preview(client, "change delivery fee to 5")
    assert fileapp.settings_store().all()["delivery_fee"] == 0
    confirm(client, r["token"])
    assert fileapp.settings_store().all()["delivery_fee"] == 5


def test_confirm_rechecks_the_order(client):
    login(client, "owner-pass")
    o = order()
    r = preview(client, "李明 cancel")
    fileapp.store.get(o.id).status = OrderStatus.DELIVERED     # delivered meanwhile
    res = confirm(client, r["token"])
    assert res.status_code == 400 and "delivered" in res.get_json()["error"]


def test_pasted_wechat_messages_keep_the_intent_card(client):
    login(client, "owner-pass")
    order()
    r = client.post("/api/agent", json={"text": "已送达 13912345678"}).get_json()
    assert r["action"] == "deliver"                             # old flow, own Deliver button
    r = client.post("/api/agent", json={"text": "取消订单 不要了 13912345678"}).get_json()
    assert r["action"] == "cancel"


def test_bad_token_and_drivers_cannot_confirm(client):
    login(client, "owner-pass")
    assert confirm(client, "nope").status_code == 400
    order()
    token = preview(client, "李明 paid")["token"]
    client.post("/logout")
    login_driver(client)
    assert confirm(client, token).status_code == 403
