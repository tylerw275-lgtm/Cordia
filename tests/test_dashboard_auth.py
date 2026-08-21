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


# --- the decision endpoint changes state, so it must be gated too -----------

import pytest


@pytest.mark.asyncio
async def test_consent_decision_requires_a_session(client, db):
    """Without this gate, anyone who found the URL could approve themselves."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.services import consent_service

    _with_password("correct horse battery staple")
    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status) "
             "VALUES (:p, :ts, 'web_form', 'pending')"),
        {"p": "+17876765645", "ts": datetime.now(timezone.utc)},
    )
    await db.commit()

    r = await client.post("/health/consent-decision",
                          data={"phone": "+17876765645", "decision": "approved"})

    assert r.status_code == 401
    assert await consent_service.is_approved(db, "+17876765645") is False


@pytest.mark.asyncio
async def test_consent_decision_with_a_session_applies(client, db):
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.services import consent_service

    _with_password("correct horse battery staple")
    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status) "
             "VALUES (:p, :ts, 'web_form', 'pending')"),
        {"p": "+17876765645", "ts": datetime.now(timezone.utc)},
    )
    await db.commit()

    client.cookies.set(d._COOKIE, d._issue_session())
    r = await client.post("/health/consent-decision",
                          data={"phone": "+17876765645", "decision": "rejected"},
                          follow_redirects=False)

    assert r.status_code == 303
    assert await consent_service.status_for(db, "+17876765645") == "rejected"
