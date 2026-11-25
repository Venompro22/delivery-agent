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


def test_customer_demo_opens_a_tracking_page_without_login(client):
    r = client.get("/try/customer")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "/track/" in loc and "?k=" in loc
    order_id, key = loc.split("/track/")[1].split("?k=")
    d = client.get(f"/api/track/{order_id}?k={key}").get_json()
    assert d["ok"] and d["status"] == "on_the_way"


def test_login_page_offers_the_three_demos(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'href="/try"' in html and "/try/driver" in html and "/try/customer" in html
