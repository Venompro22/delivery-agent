# Separate demos: a visitor can try the driver's side or the customer's side on its own.
import fileapp


def test_driver_demo_lands_on_the_driver_page(client):
    r = client.get("/try/driver")
    assert r.status_code == 302 and r.headers["Location"].endswith("/driver")
    assert client.get("/driver").status_code == 200
    stops = client.get("/api/route").get_json()["stops"]
    assert stops and all(fileapp.store.get(s["id"]).note.startswith(fileapp.DEMO_TAG) for s in stops)


def test_driver_demo_can_deliver_demo_orders(client):
    client.get("/try/driver")
    stop = client.get("/api/route").get_json()["stops"][0]
    assert client.post(f"/api/orders/{stop['id']}/deliver").get_json()["ok"]


def test_customer_demo_lands_on_the_customer_screen(client):
    r = client.get("/try/customer")
    assert r.status_code == 302 and r.headers["Location"].endswith("/me")
    assert client.get("/me").status_code == 200


def test_customer_demo_sees_its_order_on_the_way(client):
    client.get("/try/customer")
    orders = client.get("/api/me/live").get_json()["orders"]
    assert len(orders) == 1
    o = orders[0]
    assert o["status"] == "pending" and o["next"] is True     # next stop → road to the door shows
    assert fileapp.store.get(o["id"]).note.startswith(fileapp.DEMO_TAG)


def test_each_demo_visitor_gets_their_own_account(client):
    client.get("/try/customer")
    first = client.get("/api/me/live").get_json()["orders"]
    other = fileapp.app.test_client()
    other.get("/try/customer")
    with client.session_transaction() as a, other.session_transaction() as b:
        assert a["uid"] != b["uid"]
    assert first


def test_demo_customer_order_stays_demo_and_silent(client, monkeypatch):
    from test_customer_ordering import owner_adds_products, place
    water, _, _ = owner_adds_products(client)
    sent = []
    monkeypatch.setattr(fileapp, "_notify_new_request", lambda *a, **k: sent.append(a))
    client.get("/try/customer")
    d = place(client, [{"id": water["id"], "qty": 1}]).get_json()
    assert d["ok"]
    o = fileapp.store.get(d["id"])
    assert o.note.startswith(fileapp.DEMO_TAG) and o.status.value == "pending"
    assert sent == []


def test_old_demo_customers_are_cleaned_up(client, monkeypatch):
    client.get("/try/customer")
    with client.session_transaction() as sess:
        uid = sess["uid"]
    real = fileapp.time.time()
    monkeypatch.setattr(fileapp.time, "time", lambda: real + fileapp.DEMO_CUSTOMER_TTL + 60)
    fileapp._cleanup_demo_customers()
    assert fileapp.users.get(uid) is None
    assert fileapp.users.customer_order_ids(uid) == []


def test_login_page_offers_the_three_demos(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'href="/try"' in html and "/try/driver" in html and "/try/customer" in html
