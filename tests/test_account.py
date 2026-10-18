"""🔑 Changing and resetting passwords through the website."""
import fileapp


def make_driver():
    u = fileapp.users.create("13800138000", "Ali", "blue-river-42")
    fileapp.users.set_status(u["id"], "approved")
    return u


def login(c, password="blue-river-42"):
    return c.post("/login", data={"phone": "13800138000", "password": password})


def test_password_change_logs_out_other_devices(client):
    make_driver()
    other = fileapp.app.test_client()            # a second phone
    login(client)
    login(other)
    assert other.get("/driver").status_code == 200
    client.post("/account", data={"current": "blue-river-42", "new": "green-hill-77"})
    assert client.get("/driver").status_code == 200    # this phone stays in
    assert other.get("/driver").status_code == 302     # the other one is out


def test_reset_forces_a_password_change(client):
    u = make_driver()
    temp = fileapp.users.reset_password(u["id"])
    login(client, temp)
    assert client.get("/driver").status_code == 302    # sent to /account
    assert client.get("/account").status_code == 200
