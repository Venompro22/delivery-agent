"""👀 Demo viewer: sees the dashboard read-only, and only demo data."""
import fileapp
from models import Order


def _viewer(client):
    client.get("/demo?k=" + fileapp.demo_key())
    return client


def test_wrong_demo_key_is_refused(client):
    assert client.get("/demo?k=WRONG").status_code == 404


def test_viewer_can_open_the_dashboard(client):
    assert _viewer(client).get("/").status_code == 200


def test_viewer_cannot_write(client):
    assert _viewer(client).post("/api/orders", json={}).status_code == 403


def test_viewer_cannot_open_security_pages(client):
    c = _viewer(client)
    assert c.get("/security/2fa").status_code == 403
    assert c.get("/api/security-log").status_code == 403


def test_viewer_only_sees_demo_orders(client):
    fileapp.store.add(Order(customer_name="Real", lat=30.2, lng=120.2))
    fileapp.store.add(Order(customer_name="Demo", lat=30.2, lng=120.2,
                            note=fileapp.DEMO_TAG + " x"))
    names = [o["name"] for o in _viewer(client).get("/api/orders").get_json()]
    assert names == ["Demo"]


def test_viewer_gets_the_banner_and_others_dont(client):
    assert "demo-banner" in _viewer(client).get("/").get_data(as_text=True)
    other = fileapp.app.test_client()
    assert "demo-banner" not in other.get("/login").get_data(as_text=True)


def test_public_try_link_opens_read_only_demo(client):
    assert client.get("/try").status_code == 302
    assert client.post("/api/orders", json={}).status_code == 403


def test_visitor_who_opens_login_leaves_the_demo(client):
    _viewer(client)
    assert client.get("/login").status_code == 200        # login form, not a redirect home
    with client.session_transaction() as s:
        assert s.get("role") is None


def test_public_demo_can_be_turned_off(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "0")
    assert client.get("/try").headers["Location"].endswith("/login")
