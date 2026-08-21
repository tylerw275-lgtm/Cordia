"""The dashboard shows full phone numbers and the consent record, so the
session cookie must not be forgeable."""
from app.api import dashboard as d
from app.config import settings


def _with_password(pw):
    settings.dashboard_password = pw


def test_issued_session_is_accepted():
    _with_password("correct horse battery staple")
    assert d._session_valid(d._issue_session()) is True


def test_forged_signature_rejected():
    _with_password("correct horse battery staple")
    token = d._issue_session()
    expires = token.split(".")[0]
    assert d._session_valid(f"{expires}.deadbeef") is False


def test_changing_the_password_invalidates_old_sessions():
    _with_password("first")
    token = d._issue_session()
    _with_password("second")
    assert d._session_valid(token) is False


def test_no_password_configured_means_no_valid_session():
    _with_password("something")
    token = d._issue_session()
    _with_password("")
    assert d._session_valid(token) is False


def test_expired_session_rejected():
    import hashlib, hmac, time
    _with_password("pw")
    past = str(int(time.time()) - 60)
    sig = hmac.new(d._signing_key(), past.encode(), hashlib.sha256).hexdigest()
    assert d._session_valid(f"{past}.{sig}") is False


def test_garbage_rejected():
    _with_password("pw")
    for junk in (None, "", "nodot", "..", "abc.def"):
        assert d._session_valid(junk) is False
