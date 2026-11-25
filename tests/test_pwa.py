# PWA: the app can be installed on a phone home screen, by every kind of user.
import json
import os

import fileapp
from conftest import login

PW = "Str0ng!Pass2026"


def test_manifest_points_to_real_icons(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and "manifest" in r.mimetype
    m = json.loads(r.data)
    assert m["display"] == "standalone" and m["start_url"] == "/"
    for icon in m["icons"]:
        assert os.path.exists(os.path.join(fileapp.HERE, icon["src"].lstrip("/"))), icon["src"]


def test_service_worker_is_served_from_the_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["Content-Type"]
    assert r.headers["Service-Worker-Allowed"] == "/"
    assert "no-cache" in r.headers["Cache-Control"]


def test_customers_can_install_too(client):
    client.post("/signup", data={"role": "customer", "phone": "13900000009", "name": "Kehu", "password": PW})
    client.post("/login", data={"phone": "13900000009", "password": PW})
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200


def test_pages_link_the_manifest(client):
    login(client, "owner-pass")
    for page in ("/", "/orders", "/stats", "/products"):
        html = client.get(page).get_data(as_text=True)
        assert 'rel="manifest"' in html and "pwa.js" in html, page
