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
