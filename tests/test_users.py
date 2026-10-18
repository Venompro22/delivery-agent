"""🧪 Driver accounts: sign up, login, approve/block."""
import pytest

from users import UserStore, public


@pytest.fixture
def users(tmp_path):
    """A fresh, empty user database for every test."""
    return UserStore(str(tmp_path / "users.db"))


def test_new_account_is_pending(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    assert u["status"] == "pending"
    assert u["role"] == "driver"


def test_password_is_never_stored_in_clear(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    assert "blue-river-42" not in u["password_hash"]
    assert "password_hash" not in public(u)


def test_login_right_and_wrong(users):
    users.create("13800138000", "Ali", "blue-river-42")
    assert users.check_login("13800138000", "blue-river-42") is not None
    assert users.check_login("13800138000", "wrong-pass") is None
    assert users.check_login("13900000000", "blue-river-42") is None   # unknown phone


def test_same_phone_twice_is_refused(users):
    users.create("13800138000", "Ali", "blue-river-42")
    with pytest.raises(ValueError):
        users.create("13800138000", "Other", "password456")


@pytest.mark.parametrize("phone,name,password", [
    ("12345", "Ali", "blue-river-42"),          # not a China mobile
    ("13800138000", "", "blue-river-42"),       # empty name
    ("13800138000", "Ali", "short"),          # password too short
])
def test_bad_input_is_refused(users, phone, name, password):
    with pytest.raises(ValueError):
        users.create(phone, name, password)


def test_approve_then_block(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    assert users.set_status(u["id"], "approved")
    assert users.get(u["id"])["approved_at"] is not None
    assert users.set_status(u["id"], "blocked")
    assert users.get(u["id"])["status"] == "blocked"
    assert not users.set_status("no-such-id", "approved")


def test_pending_accounts_come_first(users):
    a = users.create("13800138001", "A", "blue-river-42")
    users.create("13800138002", "B", "blue-river-42")
    users.set_status(a["id"], "approved")
    assert users.all()[0]["status"] == "pending"
    

@pytest.mark.parametrize("password", [
    "12345678", "88888888", "woaini1314", "Password1", "abcdefgh", "11112222", "13800138000",
])
def test_weak_passwords_are_refused(users, password):
    with pytest.raises(ValueError):
        users.create("13800138000", "Ali", password)


def test_a_good_password_is_accepted(users):
    assert users.create("13800138000", "Ali", "blue-river-42")["status"] == "pending"


def test_set_password_bumps_the_version(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    assert u["pw_version"] == 0
    users.set_password(u["id"], "green-hill-77")
    assert users.check_login("13800138000", "green-hill-77")
    assert not users.check_login("13800138000", "blue-river-42")
    assert users.get(u["id"])["pw_version"] == 1


def test_set_password_refuses_weak(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    with pytest.raises(ValueError):
        users.set_password(u["id"], "12345678")


def test_reset_password_forces_a_change(users):
    u = users.create("13800138000", "Ali", "blue-river-42")
    temp = users.reset_password(u["id"])
    assert len(temp) == 12
    assert users.check_login("13800138000", temp)
    assert users.get(u["id"])["must_change"] == 1
    users.set_password(u["id"], "green-hill-77")
    assert users.get(u["id"])["must_change"] == 0
