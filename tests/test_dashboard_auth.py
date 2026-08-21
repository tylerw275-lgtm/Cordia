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


# --- a secret in a URL ends up in every access log it passes through --------

@pytest.mark.asyncio
async def test_the_query_param_secret_no_longer_works(client, mocker):
    """Using the dashboard used to write the admin secret into Railway's logs
    on every visit. Header and cookie both stay; the URL form is gone."""
    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    r = await client.get("/health/dashboard?secret=s3cret")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_the_header_secret_still_works_for_scripts(client, mocker):
    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    r = await client.get("/health/dashboard", headers={"X-Admin-Secret": "s3cret"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_the_dashboard_never_puts_a_secret_in_its_own_links(client, mocker):
    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "?secret=" not in html
    assert "s3cret" not in html
    assert 'href="/health/config"' in html


@pytest.mark.asyncio
async def test_admin_routes_accept_the_dashboard_session(client, mocker):
    """This is what lets the dashboard link to them without a URL secret — the
    cookie is scoped to /health, which covers these routes."""
    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    client.cookies.set(d._COOKIE, d._issue_session())
    assert (await client.get("/health/config")).status_code == 200


@pytest.mark.asyncio
async def test_admin_routes_reject_the_query_param(client, mocker):
    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    assert (await client.get("/health/config?secret=s3cret")).status_code == 401
    r = await client.get("/health/config", headers={"X-Admin-Secret": "s3cret"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_routes_still_fail_closed_with_no_secret_configured(client, mocker):
    """These expose family PII and full conversation text."""
    mocker.patch.object(settings, "dashboard_password", "")
    mocker.patch.object(settings, "admin_api_secret", "")

    assert (await client.get("/health/config")).status_code == 401
    assert (await client.get("/health/config", headers={"X-Admin-Secret": ""})).status_code == 401
