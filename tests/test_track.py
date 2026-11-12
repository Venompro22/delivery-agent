"""🔗 Tests for signed tracking links."""
import fileapp


def test_track_key_is_stable_and_unique():
    k = fileapp.track_key("a3f9c2e1")
    assert k == fileapp.track_key("a3f9c2e1")        # same order → same key
    assert k != fileapp.track_key("a3f9c2e2")        # one letter changed → totally different key
    assert len(k) == 22


def test_tracking_needs_the_right_key(client):
    assert client.get("/api/track/a3f9c2e1").status_code == 404           # no key
    assert client.get("/api/track/a3f9c2e1?k=wrong").status_code == 404   # wrong key
    assert client.get("/api/track/a3f9c2e1?k=é").status_code == 404       # non-ASCII must not crash


def test_track_gives_times_for_timeline_and_countdown(client):
    import fileapp
    from models import Order
    o = Order(customer_name="T", lat=30.25, lng=119.72, note=fileapp.DEMO_TAG + " 2桶水 8号楼")
    fileapp.store.add(o)
    d = client.get(f"/api/track/{o.id}?k={fileapp.track_key(o.id)}").get_json()
    assert d["created_at"] and "eta_epoch" in d
    assert d["note"] == "2桶水 8号楼"            # internal demo tag never shown to customers


def test_track_shows_shop_phone_only_when_set(client, monkeypatch):
    import fileapp
    from models import Order
    o = Order(customer_name="P", lat=30.25, lng=119.72)
    fileapp.store.add(o)
    url = f"/api/track/{o.id}?k={fileapp.track_key(o.id)}"
    assert client.get(url).get_json()["shop_phone"] == ""
    monkeypatch.setenv("SHOP_PHONE", "13800000000")
    assert client.get(url).get_json()["shop_phone"] == "13800000000"


def test_new_order_comes_with_a_ready_wechat_reply(client):
    import fileapp
    with client.session_transaction() as s:
        s["auth"] = True; s["role"] = "owner"; s["v"] = fileapp.session_version()
    d = client.post("/api/orders", json={"name": "N", "lat": 30.25, "lng": 119.72}).get_json()
    assert d["ok"] and "/track/" + d["id"] in d["track_url"] and "k=" in d["track_url"]
    assert d["track_url"] in d["reply"]