"""🧾 Customer accounts: no approval needed, and they only ever see their own orders."""
import fileapp
from models import Order

PW = "Str0ng!Pass2026"


def _customer(client, phone="13900000001"):
    client.post("/signup", data={"role": "customer", "phone": phone, "name": "Kehu", "password": PW})
    return client.post("/login", data={"phone": phone, "password": PW})


def test_customer_signup_is_active_right_away(client):
    r = _customer(client)
    assert r.status_code == 302 and r.headers["Location"].endswith("/me")


def test_customer_cannot_reach_driver_or_owner_apis(client):
    _customer(client)
    assert client.get("/api/route").status_code == 403
    assert client.get("/api/orders").status_code == 403
    assert client.get("/").headers["Location"].endswith("/me")


def test_customer_claims_an_order_with_its_tracking_link(client):
    o = Order(customer_name="Me", lat=30.25, lng=119.72)
    fileapp.store.add(o)
    _customer(client)
    assert client.post("/api/me/claim", json={"link": f"/track/{o.id}?k=WRONG"}).status_code == 400
    ok = client.post("/api/me/claim", json={"link": f"https://x/track/{o.id}?k={fileapp.track_key(o.id)}"})
    assert ok.get_json()["ok"]
    assert [x["id"] for x in client.get("/api/me/orders").get_json()["orders"]] == [o.id]


def test_drivers_list_never_shows_customers(client):
    _customer(client)
    assert all(u["role"] == "driver" for u in fileapp.users.all())
