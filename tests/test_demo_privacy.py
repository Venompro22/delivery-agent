"""Demo visitors never see where real drivers are."""
import fileapp


def test_demo_visitor_never_sees_driver_gps(client):
    u = fileapp.users.create("13900139033", "Real Driver", "Str0ng!Pass2026")
    fileapp.users.set_status(u["id"], "approved")
    fileapp.users.set_location(u["id"], 30.25, 119.72)
    client.get("/try")
    rows = client.get("/api/drivers").get_json()
    assert rows and all("lat" not in r and "lng" not in r and "located_at" not in r for r in rows)
