"""🔴 Emergency kill switch: signed links freeze or close the whole site."""
import pytest

import fileapp


@pytest.fixture(autouse=True)
def reset_mode(tmp_path, monkeypatch):
    """Each test gets its own panic file, and the real one is never touched."""
    monkeypatch.setattr(fileapp, "PANIC_PATH", str(tmp_path / ".panic_mode"))
    fileapp.app.config["TESTING"] = True
    yield


@pytest.fixture
def client():
    return fileapp.app.test_client()


def _switch(client, mode):
    return client.get(f"/panic/{mode}?k={fileapp.panic_key(mode)}")


def test_default_mode_is_normal():
    assert fileapp.panic_mode() == "normal"


def test_signed_link_switches_mode(client):
    assert _switch(client, "readonly").status_code == 200
    assert fileapp.panic_mode() == "readonly"


def test_wrong_key_is_refused(client):
    r = client.get("/panic/off?k=WRONG")
    assert r.status_code == 404
    assert fileapp.panic_mode() == "normal"      # nothing changed


def test_unknown_mode_is_refused(client):
    r = client.get(f"/panic/banana?k=whatever")
    assert r.status_code == 404


def test_readonly_blocks_writes_but_allows_reads(client):
    _switch(client, "readonly")
    assert client.get("/login").status_code == 200
    assert client.post("/api/orders", json={}).status_code == 503


def test_off_blocks_everything(client):
    _switch(client, "off")
    assert client.get("/login").status_code == 503
    assert client.get("/api/orders").status_code == 503


def test_panic_link_still_works_while_off(client):
    """You must never be able to lock yourself out."""
    _switch(client, "off")
    assert _switch(client, "normal").status_code == 200
    assert fileapp.panic_mode() == "normal"


def test_lockdown_still_lets_customers_track(client):
    _switch(client, "lockdown")
    assert client.get("/login").status_code == 503
    # a tracking URL is not blocked (the key check inside decides)
    assert client.get("/api/track/abc123?k=x").status_code != 503
