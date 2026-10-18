"""📍 Each driver has their own GPS position."""
import fileapp
from conftest import login_driver


def test_each_driver_has_own_location(client):
    login_driver(client)
    default = (fileapp.driver.lat, fileapp.driver.lng)
    client.post("/api/driver/location", json={"lat": 30.25, "lng": 120.16})
    me = fileapp.users.by_phone("13900139000")
    assert (me["lat"], me["lng"]) == (30.25, 120.16)          # saved on the account
    assert (fileapp.driver.lat, fileapp.driver.lng) == default  # the shared point did not move
    start = client.get("/api/route").get_json()["driver"]
    assert (start["lat"], start["lng"]) == (30.25, 120.16)     # the route starts here


def test_bad_location_is_refused(client):
    login_driver(client)
    assert client.post("/api/driver/location", json={"lat": "nan", "lng": 120}).status_code == 400


from conftest import login  # noqa: E402


def test_owner_sees_every_driver_on_the_map(client):
    phone = fileapp.app.test_client()                    # the driver's phone
    login_driver(phone)
    phone.post("/api/driver/location", json={"lat": 30.25, "lng": 120.16})
    login(client, "owner-pass")
    drivers = client.get("/api/route").get_json()["drivers"]
    assert [(d["name"], d["lat"], d["lng"]) for d in drivers] == [("Test Driver", 30.25, 120.16)]


def test_drivers_dont_see_each_other(client):
    login_driver(client)
    assert "drivers" not in client.get("/api/route").get_json()
