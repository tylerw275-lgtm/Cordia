"""The consent form is a public link, so signing it must not grant access.

Consent is legal evidence and is never edited away; approval is a separate
access decision Cordia makes. A stranger who finds the link gets a valid
consent record and no way in.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.services import consent_service
from app.tools import consent_tools

STRANGER = "+17876765645"
KNOWN = "+16155029999"


async def _sign(db, phone, status="pending", name="Someone", opted_out=False):
    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status, opted_out_at) "
             "VALUES (:p, :ts, 'web_form', :s, :out) "
             "ON CONFLICT (phone) DO UPDATE SET approval_status = EXCLUDED.approval_status"),
        {"p": phone, "ts": datetime.now(timezone.utc), "s": status,
         "out": datetime.now(timezone.utc) if opted_out else None},
    )
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS consent_submissions (id SERIAL PRIMARY KEY, "
        "full_name TEXT NOT NULL, phone VARCHAR(20) NOT NULL, submitted_at TIMESTAMPTZ NOT NULL)"
    ))
    await db.execute(
        text("INSERT INTO consent_submissions (full_name, phone, submitted_at) "
             "VALUES (:n, :p, :ts)"),
        {"n": name, "p": phone, "ts": datetime.now(timezone.utc)},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_signing_the_form_does_not_grant_access(db):
    await _sign(db, STRANGER)
    assert await consent_service.is_approved(db, STRANGER) is False
    assert await consent_service.status_for(db, STRANGER) == "pending"


@pytest.mark.asyncio
async def test_approval_grants_access(db):
    await _sign(db, KNOWN)
    assert await consent_service.set_status(db, KNOWN, "approved") is True
    assert await consent_service.is_approved(db, KNOWN) is True


@pytest.mark.asyncio
async def test_rejection_keeps_the_consent_record_but_denies_access(db):
    """The record is compliance evidence — rejecting must never delete it."""
    await _sign(db, STRANGER)
    await consent_service.set_status(db, STRANGER, "rejected")
    assert await consent_service.is_approved(db, STRANGER) is False
    row = (await db.execute(
        text("SELECT consented_at, method FROM sms_consent WHERE phone = :p"),
        {"p": STRANGER},
    )).first()
    assert row is not None and row.consented_at is not None
    assert row.method == "web_form"


@pytest.mark.asyncio
async def test_opt_out_beats_approval(db):
    await _sign(db, KNOWN, status="approved", opted_out=True)
    assert await consent_service.is_approved(db, KNOWN) is False
    assert await consent_service.status_for(db, KNOWN) == "opted_out"


@pytest.mark.asyncio
async def test_pending_list_carries_the_name_they_typed(db):
    await _sign(db, STRANGER, name="Jane Doe")
    pending = await consent_service.list_pending(db)
    assert [p["name"] for p in pending] == ["Jane Doe"]


@pytest.mark.asyncio
async def test_approved_and_rejected_drop_off_the_pending_list(db):
    await _sign(db, STRANGER)
    await consent_service.set_status(db, STRANGER, "rejected")
    assert await consent_service.list_pending(db) == []


@pytest.mark.asyncio
async def test_last_four_digits_resolve_a_single_pending_request(db):
    await _sign(db, STRANGER, name="Jane Doe")
    result = await consent_tools.approve_consent_request_handler(db, phone="5645")
    assert result["ok"] is True
    assert await consent_service.is_approved(db, STRANGER) is True


@pytest.mark.asyncio
async def test_ambiguous_last_four_never_guesses_who_to_let_in(db):
    await _sign(db, "+15551234444", name="A")
    await _sign(db, "+15559994444", name="B")
    result = await consent_tools.approve_consent_request_handler(db, phone="4444")
    assert result["ok"] is False and result["reason"] == "ambiguous"
    assert await consent_service.is_approved(db, "+15551234444") is False
    assert await consent_service.is_approved(db, "+15559994444") is False


@pytest.mark.asyncio
async def test_approving_a_number_that_never_signed_reports_plainly(db):
    result = await consent_tools.approve_consent_request_handler(db, phone="+15550001111")
    assert result["ok"] is False and result["reason"] == "no_consent_record"


@pytest.mark.asyncio
async def test_outbound_will_not_text_an_unapproved_number(db):
    from app.tools import outbound_tools
    await _sign(db, STRANGER)
    assert await outbound_tools._sms_consent_ok(db, STRANGER) is False
    await consent_service.set_status(db, STRANGER, "approved")
    assert await outbound_tools._sms_consent_ok(db, STRANGER) is True


# --- end-to-end through the real webhook ------------------------------------

async def _roster_member(db, phone, name="Pat Stranger"):
    """Someone on the roster with circle access — i.e. everything the old code
    checked. Only approval should still be standing between them and Cord."""
    from app.models.family import FamilyMember
    m = FamilyMember(name=name, phone=phone, relationship="child",
                     has_circle_access=True, circle_consented_at=datetime.now(timezone.utc))
    db.add(m)
    await db.commit()


@pytest.mark.asyncio
async def test_webhook_ignores_a_consented_but_unapproved_sender(db, client, mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    send = mocker.patch("app.services.sms_service.send_sms")
    chat = mocker.patch("app.services.claude_service.chat", return_value="hello")
    await _roster_member(db, STRANGER)
    await _sign(db, STRANGER)

    r = await client.post("/webhook/sms", data={
        "From": STRANGER, "Body": "let me in", "MessageSid": "SM-unapproved"})

    assert r.status_code == 200
    assert not chat.called, "an unapproved number reached the assistant"
    assert not send.called, "an unapproved number got a reply"


@pytest.mark.asyncio
async def test_webhook_lets_the_same_sender_in_once_approved(db, client, mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    mocker.patch("app.services.sms_service.send_sms")
    chat = mocker.patch("app.services.claude_service.chat", return_value="hello")
    await _roster_member(db, STRANGER)
    await _sign(db, STRANGER, status="approved")

    r = await client.post("/webhook/sms", data={
        "From": STRANGER, "Body": "let me in", "MessageSid": "SM-approved"})

    assert r.status_code == 200
    assert chat.called


@pytest.mark.asyncio
async def test_stop_still_works_for_an_unapproved_number(db, client, mocker):
    """Opt-out is carrier-mandated: it must never depend on being approved."""
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    send = mocker.patch("app.services.sms_service.send_sms")
    await _roster_member(db, STRANGER)
    await _sign(db, STRANGER)

    r = await client.post("/webhook/sms", data={
        "From": STRANGER, "Body": "STOP", "MessageSid": "SM-stop"})

    assert r.status_code == 200
    assert send.called, "STOP was not acknowledged"
    assert await consent_service.status_for(db, STRANGER) == "opted_out"


# --- rejected people come off the dashboard --------------------------------

@pytest.mark.asyncio
async def test_a_rejected_number_disappears_from_the_dashboard(db, client, mocker):
    """She asked for them gone once she has decided — a growing list of blocked
    numbers is noise. The consent row stays as the legal record."""
    from sqlalchemy import text as sa_text

    from app.api import dashboard as d
    from app.config import settings as cfg

    mocker.patch.object(cfg, "dashboard_password", "pw")
    await _sign(db, STRANGER, name="Unknown Caller")

    client.cookies.set(d._COOKIE, d._issue_session())
    before = (await client.get("/health/dashboard")).text
    assert "(787) 676-5645" in before, "a pending number should be visible to decide on"

    await consent_service.set_status(db, STRANGER, "rejected")
    after = (await client.get("/health/dashboard")).text

    assert "(787) 676-5645" not in after
    assert "Unknown Caller" not in after

    # Gone from the screen, still on file — this is compliance evidence.
    row = (await db.execute(
        sa_text("SELECT approval_status, consented_at FROM sms_consent WHERE phone = :p"),
        {"p": STRANGER},
    )).first()
    assert row.approval_status == "rejected"
    assert row.consented_at is not None


@pytest.mark.asyncio
async def test_a_rejected_number_never_slips_into_who_cord_can_text(db, client, mocker):
    """Hiding them must not mean losing track of them: a rejected number that
    fell through the classification would land in the 'can text' table."""
    from app.api import dashboard as d
    from app.config import settings as cfg

    mocker.patch.object(cfg, "dashboard_password", "pw")
    await _sign(db, STRANGER, name="Unknown Caller")
    await consent_service.set_status(db, STRANGER, "rejected")

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "Who Cord can text" in html
    assert "5645" not in html
    assert await consent_service.is_approved(db, STRANGER) is False
