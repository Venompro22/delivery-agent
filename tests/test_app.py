"""🧪 Automatic checks for the Delivery Agent. Run: python3 -m pytest -q"""
from conftest import login
import fileapp


def test_login_page_loads(client):
    assert client.get("/login").status_code == 200


def test_owner_can_log_in(client):
    r = login(client, "owner-pass")
    assert r.status_code == 302
    assert client.get("/").status_code == 200


def test_wrong_password_is_rejected(client):
    r = login(client, "nope")
    assert b"Wrong password" in r.data
    assert client.get("/api/route").status_code == 401


def test_api_needs_login(client):
    assert client.get("/api/route").status_code == 401


def test_driver_lands_on_driver_page(client):
    r = login(client, "driver-pass")
    assert r.headers["Location"].endswith("/driver")


def test_driver_cannot_see_owner_pages(client):
    login(client, "driver-pass")
    assert client.get("/stats").headers["Location"].endswith("/driver")
    assert client.get("/api/stats").status_code == 403


def test_driver_can_use_driver_tools(client):
    login(client, "driver-pass")
    assert client.get("/driver").status_code == 200
    assert client.get("/api/route").status_code == 200


def test_owner_adds_order_and_it_appears_in_route(client):
    login(client, "owner-pass")
    r = client.post("/api/orders", json={"name": "李明", "lat": "30.27", "lng": "120.16"})
    assert r.get_json()["ok"] is True
    assert client.get("/api/route").get_json()["summary"]["stops"] == 1


def test_bad_order_is_refused(client):
    login(client, "owner-pass")
    r = client.post("/api/orders", json={"name": "", "lat": "999", "lng": "x"})
    assert r.status_code == 400


def test_logout_all_kicks_out_other_devices(client):
    driver = fileapp.app.test_client()
    login(driver, "driver-pass")
    assert driver.get("/api/route").status_code == 200

    login(client, "owner-pass")
    assert client.post("/api/logout-all").get_json()["ok"] is True
    assert driver.get("/api/route").status_code == 401


def test_security_headers_are_sent(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
