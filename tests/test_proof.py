"""📷 Drivers upload a delivery photo; only authorized users can see it."""
import io

import pytest

import fileapp
from models import Order, OrderStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with fresh temp stores and a temp proof folder."""
    from store import OrderStore
    from users import UserStore
    monkeypatch.setattr(fileapp, "store", OrderStore(str(tmp_path / "o.db")))
    monkeypatch.setattr(fileapp, "users", UserStore(str(tmp_path / "u.db")))
    monkeypatch.setattr(fileapp, "PROOF_DIR", str(tmp_path / "proofs"))
    import os
    os.makedirs(fileapp.PROOF_DIR, exist_ok=True)
    fileapp.app.config["TESTING"] = True
    c = fileapp.app.test_client()
    with c.session_transaction() as sess:                 # log in as owner
        sess["auth"] = True
        sess["role"] = "owner"
        sess["v"] = fileapp.session_version()
    return c


def _delivered(order_id_driver="d1"):
    o = Order(customer_name="Cust", lat=30.0, lng=120.0, driver_id=order_id_driver)
    o.status = OrderStatus.DELIVERED
    return o


def _jpeg():
    return (io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 50), "p.jpg", "image/jpeg")


def test_upload_and_download(client):
    o = _delivered()
    fileapp.store.orders.append(o)
    up = client.post(f"/api/orders/{o.id}/proof",
                     data={"photo": _jpeg()}, content_type="multipart/form-data")
    assert up.get_json() == {"ok": True}
    assert fileapp.store.get(o.id).proof == f"{o.id}.jpg"
    got = client.get(f"/api/orders/{o.id}/proof")
    assert got.status_code == 200


def test_reject_non_image(client):
    o = _delivered()
    fileapp.store.orders.append(o)
    bad = (io.BytesIO(b"hello"), "note.txt", "text/plain")
    up = client.post(f"/api/orders/{o.id}/proof",
                     data={"photo": bad}, content_type="multipart/form-data")
    assert up.status_code == 400
    assert fileapp.store.get(o.id).proof is None


def test_cannot_upload_to_undelivered(client):
    o = Order(customer_name="Cust", lat=30.0, lng=120.0, driver_id="d1")  # pending
    fileapp.store.orders.append(o)
    up = client.post(f"/api/orders/{o.id}/proof",
                     data={"photo": _jpeg()}, content_type="multipart/form-data")
    assert up.status_code == 400


def test_download_missing_photo_is_404(client):
    o = _delivered()
    fileapp.store.orders.append(o)
    got = client.get(f"/api/orders/{o.id}/proof")      # never uploaded
    assert got.status_code == 404
