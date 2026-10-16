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
