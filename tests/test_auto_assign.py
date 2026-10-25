"""🚚 New orders auto-assign to the nearest approved driver (or the pool)."""
from users import UserStore, find_nearest_driver


def _driver(u, phone, name, lat, lng, status="approved"):
    """Helper: make a driver, set status + GPS, return the dict."""
    d = u.create(phone, name, "GoodPass2026!")
    u.set_status(d["id"], status)
    if lat is not None:
        u.set_location(d["id"], lat, lng)
    return d


def test_picks_the_nearest_driver(tmp_path):
    u = UserStore(str(tmp_path / "u.db"))
    near = _driver(u, "13800000001", "Near", 30.260, 120.220)
    _driver(u, "13800000002", "Far", 31.000, 121.000)      # ~100+ km away

    pick = find_nearest_driver(u, 30.261, 120.221)
    assert pick is not None and pick["id"] == near["id"]


def test_skips_blocked_and_pending(tmp_path):
    u = UserStore(str(tmp_path / "u.db"))
    _driver(u, "13800000001", "Blocked", 30.260, 120.220, status="blocked")
    _driver(u, "13800000002", "Pending", 30.261, 120.221, status="pending")
    ok = _driver(u, "13800000003", "Ok", 30.500, 120.500)  # far but approved

    pick = find_nearest_driver(u, 30.261, 120.221)
    assert pick is not None and pick["id"] == ok["id"]      # only the approved one


def test_ignores_drivers_without_gps(tmp_path):
    u = UserStore(str(tmp_path / "u.db"))
    _driver(u, "13800000001", "NoGPS", None, None)          # approved, no location
    withgps = _driver(u, "13800000002", "HasGPS", 30.500, 120.500)

    pick = find_nearest_driver(u, 30.261, 120.221)
    assert pick is not None and pick["id"] == withgps["id"]


def test_returns_none_when_nobody_available(tmp_path):
    u = UserStore(str(tmp_path / "u.db"))
    _driver(u, "13800000001", "Blocked", 30.260, 120.220, status="blocked")

    pick = find_nearest_driver(u, 30.261, 120.221)
    assert pick is None                                     # order stays in the pool
