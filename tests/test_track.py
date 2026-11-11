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
