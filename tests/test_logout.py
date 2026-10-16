"""🚪 Logout must be a POST."""


def test_logout_needs_post(client):
    client.post("/login", data={"password": "owner-pass"})
    assert client.get("/logout").status_code == 405     # GET is refused
    assert client.get("/").status_code == 200           # ...so we are still logged in
    client.post("/logout")
    assert client.get("/").status_code == 302           # logged out → sent to /login
