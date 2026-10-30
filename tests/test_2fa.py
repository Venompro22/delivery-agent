"""🔐 Two-factor for the owner: TOTP codes and one-time recovery codes."""
import pyotp
import pytest
from werkzeug.security import generate_password_hash

import fileapp


@pytest.fixture
def twofa(tmp_path, monkeypatch):
    """A temp 2FA file, so the real one is never touched."""
    monkeypatch.setattr(fileapp, "TWOFA_PATH", str(tmp_path / ".owner_2fa"))
    fileapp.app.config["TESTING"] = True
    return fileapp.app


def test_disabled_by_default(twofa):
    assert fileapp.twofa_enabled() is False


def test_any_code_passes_when_disabled(twofa):
    with twofa.test_request_context("/"):
        assert fileapp.twofa_check("") is True       # 2FA off: nothing to check


def test_correct_totp_is_accepted(twofa):
    secret = pyotp.random_base32()
    fileapp.twofa_save({"secret": secret, "recovery": []})
    with twofa.test_request_context("/"):
        assert fileapp.twofa_check(pyotp.TOTP(secret).now()) is True


def test_wrong_code_is_rejected(twofa):
    fileapp.twofa_save({"secret": pyotp.random_base32(), "recovery": []})
    with twofa.test_request_context("/"):
        assert fileapp.twofa_check("000000") is False


def test_recovery_code_works_once(twofa):
    fileapp.twofa_save({"secret": pyotp.random_base32(),
                        "recovery": [generate_password_hash("abcd1234")]})
    with twofa.test_request_context("/"):
        assert fileapp.twofa_check("abcd1234") is True     # first use
        assert fileapp.twofa_check("abcd1234") is False    # burned
    assert fileapp.twofa_load()["recovery"] == []


def test_each_recovery_code_is_independent(twofa):
    fileapp.twofa_save({"secret": pyotp.random_base32(),
                        "recovery": [generate_password_hash("aaaa1111"),
                                     generate_password_hash("bbbb2222")]})
    with twofa.test_request_context("/"):
        assert fileapp.twofa_check("aaaa1111") is True
        assert fileapp.twofa_check("bbbb2222") is True     # the other still works
    assert fileapp.twofa_load()["recovery"] == []
