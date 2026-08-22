"""One person, two channels, one answer about whether they may be here.

Approval was enforced on the SMS path and nowhere else. Rejecting somebody in
the dashboard stopped them texting and did nothing at all about email, so the
access Cordia thought she had revoked was still live. In the same release,
Cord's roster tool read consent without approval — it announced that someone
had consented, and the very next tool call refused to text them.

These are the gates, tested where they are actually crossed.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.api import sms
from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.family import FamilyMember
from app.services import email_inbound

RELATIVE_PHONE = "+16155551212"
RELATIVE_EMAIL = "relative@example.com"


async def _consent(db, phone, status="approved", opted_out=False):
    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status, opted_out_at) "
             "VALUES (:p, :ts, 'web_form', :s, :out) "
             "ON CONFLICT (phone) DO UPDATE SET approval_status = EXCLUDED.approval_status, "
             "opted_out_at = EXCLUDED.opted_out_at"),
        {"p": phone, "ts": datetime.now(timezone.utc), "s": status,
         "out": datetime.now(timezone.utc) if opted_out else None},
    )
    await db.commit()


async def _relative(db, **kw):
    member = FamilyMember(
        name="Sarah Harrington", relationship="daughter", has_circle_access=True,
        phone=RELATIVE_PHONE, email=RELATIVE_EMAIL, circle_consented_at=datetime.now(timezone.utc),
        **kw,
    )
    db.add(member)
    await db.commit()
    return member


# --- email is gated by the same decision as SMS -----------------------------

@pytest.mark.parametrize("status", ["rejected", "pending"])
@pytest.mark.asyncio
async def test_email_respects_the_approval_cordia_made(db, status):
    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status=status)

    role, member, _ = await email_inbound._resolve_sender(db, RELATIVE_EMAIL)

    assert role == "unapproved", f"{status} still had a full conversation by email"
    assert member.name == "Sarah Harrington"


@pytest.mark.asyncio
async def test_an_approved_relative_still_gets_through_by_email(db):
    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="approved")

    role, _, _ = await email_inbound._resolve_sender(db, RELATIVE_EMAIL)
    assert role == "family"


@pytest.mark.asyncio
async def test_a_rejected_sender_gets_no_reply_at_all(db, mocker):
    """Not an explanation either — a reply would confirm the roster to whoever
    is probing it.

    Checked by recipient rather than by "was send_email called", because the
    operator now gets an alert about the drop through the same function. Nothing
    goes to *them*; something does go to us."""
    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="rejected")
    send = mocker.patch("app.services.email_service.send_email",
                        new=mocker.AsyncMock(return_value={"sent": True}))
    chat = mocker.patch("app.services.claude_service.chat", new=mocker.AsyncMock())

    status = await email_inbound.process_inbound_email(
        db, RELATIVE_EMAIL, "hello", "are you there"
    )

    assert status == "ignored_unapproved_sender"
    recipients = [c.kwargs.get("to") for c in send.await_args_list]
    assert RELATIVE_EMAIL not in recipients, "the rejected sender was written back to"
    assert not chat.called, "a rejected sender was still billed for a model turn"


@pytest.mark.asyncio
async def test_a_relative_with_no_number_is_not_locked_out_by_a_missing_row(db):
    """Consent is keyed by phone. Someone Cordia added with only an email has
    no row to check, and that is not the same as being rejected."""
    member = FamilyMember(name="Karie's Cousin", relationship="cousin",
                          has_circle_access=True, email="cousin@example.com")
    db.add(member)
    await db.commit()

    role, _, _ = await email_inbound._resolve_sender(db, "cousin@example.com")
    assert role == "family"


# --- STOP means the turn does not run, not just that the reply is dropped ---

@pytest.mark.asyncio
async def test_a_principal_who_texted_stop_is_not_billed_for_silence(db):
    db.add(AuthorizedUser(name="Cordia Harrington", phone="+16157080002", is_owner=True))
    await db.commit()
    await _consent(db, "+16157080002", status="approved", opted_out=True)

    role, _ = await sms._resolve_sender(db, "+16157080002")

    assert role == "opted_out", (
        "she resolved as owner after STOP, so the whole turn ran and billed "
        "before send_sms quietly dropped the answer"
    )


@pytest.mark.asyncio
async def test_no_model_turn_runs_for_an_opted_out_sender(db, mocker):
    db.add(AuthorizedUser(name="Cordia Harrington", phone="+16157080002", is_owner=True))
    await db.commit()
    await _consent(db, "+16157080002", status="approved", opted_out=True)
    chat = mocker.patch("app.services.claude_service.chat", new=mocker.AsyncMock())
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    await sms._process_inbound(db, "+16157080002", "are you there", [])

    assert not chat.called


@pytest.mark.asyncio
async def test_start_still_brings_her_back(db, mocker):
    """The gate must not swallow the one word that undoes it."""
    db.add(AuthorizedUser(name="Cordia Harrington", phone="+16157080002", is_owner=True))
    await db.commit()
    await _consent(db, "+16157080002", status="approved", opted_out=True)
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    await sms._process_inbound(db, "+16157080002", "START", [])

    assert send.called
    from app.services import consent_service
    assert await consent_service.status_for(db, "+16157080002") != "opted_out"


@pytest.mark.asyncio
async def test_an_opted_out_relative_is_also_left_alone(db):
    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="approved", opted_out=True)

    role, _ = await sms._resolve_sender(db, RELATIVE_PHONE)
    assert role == "opted_out"


# --- the roster tells the truth about who can be texted ---------------------

@pytest.mark.asyncio
async def test_the_roster_does_not_call_an_unapproved_person_consented(db):
    """What Cordia saw: Cord said Tom had consented, then one call later said
    nothing was sent."""
    from app.tools import contact_tools

    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="pending")

    roster = await contact_tools.list_sms_roster_handler(db)

    assert "Sarah Harrington" not in roster["consented"]
    assert "Sarah Harrington" in roster["awaiting_cordias_approval"]


@pytest.mark.asyncio
async def test_a_rejected_person_is_reported_as_rejected(db):
    from app.tools import contact_tools

    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="rejected")

    roster = await contact_tools.list_sms_roster_handler(db)
    assert "Sarah Harrington" in roster["rejected"]
    assert "Sarah Harrington" not in roster["consented"]


@pytest.mark.asyncio
async def test_an_approved_person_is_still_listed_as_consented(db):
    from app.tools import contact_tools

    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="approved")

    roster = await contact_tools.list_sms_roster_handler(db)
    assert "Sarah Harrington" in roster["consented"]


@pytest.mark.asyncio
async def test_the_roster_and_the_send_gate_agree(db):
    """The two answers Cord gave in one conversation, now checked against each
    other for every state a person can be in."""
    from app.services import consent_service
    from app.tools import contact_tools

    await _relative(db)
    for status in ("approved", "pending", "rejected"):
        await _consent(db, RELATIVE_PHONE, status=status)
        roster = await contact_tools.list_sms_roster_handler(db)
        listed_as_reachable = "Sarah Harrington" in roster["consented"]
        actually_reachable = await consent_service.is_approved(db, RELATIVE_PHONE)
        assert listed_as_reachable is actually_reachable, (
            f"roster and send gate disagree for {status}"
        )


# --- a report goes to whoever asked for it ----------------------------------

@pytest.mark.asyncio
async def test_a_report_goes_to_the_principal_who_asked(db, mocker):
    """Tom finishing a project used to mail the result to Cordia — in the very
    release built to keep their workspaces apart."""
    from app.tools import email_tools

    send = mocker.patch("app.services.email_service.send_email",
                        new=mocker.AsyncMock(return_value={"sent": True}))
    mocker.patch.object(settings, "owner_email", "cordia@example.com")
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001", email="tom@example.com")

    result = await email_tools.send_report_email_handler(
        db, acting_user=tom, subject="Car options", body_markdown="## Options",
    )

    assert result["to"] == "tom@example.com"
    assert send.await_args.kwargs["to"] == "tom@example.com"


@pytest.mark.asyncio
async def test_a_report_falls_back_to_the_account_holder(db, mocker):
    """A scheduled job or a deployment without PRINCIPALS_JSON has no principal
    on the turn, and the report should still land somewhere."""
    from app.tools import email_tools

    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    mocker.patch.object(settings, "owner_email", "cordia@example.com")

    result = await email_tools.send_report_email_handler(
        db, subject="Brief", body_markdown="## Brief"
    )
    assert result["to"] == "cordia@example.com"


@pytest.mark.asyncio
async def test_a_principal_with_no_address_is_named_in_the_message(db, mocker):
    """So Cord asks the right person for their address instead of asking
    Cordia for hers."""
    from app.tools import email_tools

    mocker.patch.object(settings, "owner_email", "")
    karie = AuthorizedUser(name="Karie Hampton", phone="+16153101552")

    result = await email_tools.send_report_email_handler(
        db, acting_user=karie, subject="Brief", body_markdown="## Brief"
    )
    assert result["sent"] is False
    assert "Karie" in result["message"]


# --- one decision, both channels -------------------------------------------

@pytest.mark.asyncio
async def test_both_channels_ask_the_same_question(db):
    """The two resolvers were separate functions, and each fix landed on
    whichever channel someone happened to be looking at. They share one now."""
    from app.services import access

    await _relative(db)
    for status, expected in (("approved", "family"), ("pending", "unapproved"),
                             ("rejected", "unapproved")):
        await _consent(db, RELATIVE_PHONE, status=status)

        by_sms, _ = await sms._resolve_sender(db, RELATIVE_PHONE)
        by_email, _, _ = await email_inbound._resolve_sender(db, RELATIVE_EMAIL)
        shared = await access.resolve(db, phone=RELATIVE_PHONE)

        assert by_sms == expected, f"sms said {by_sms} for {status}"
        assert by_email == expected, f"email said {by_email} for {status}"
        assert shared.role == expected


@pytest.mark.asyncio
async def test_a_stop_text_does_not_also_silence_email(db):
    """STOP ends the SMS program. It is not a request never to be answered
    again on a channel they themselves just wrote in on."""
    await _relative(db)
    await _consent(db, RELATIVE_PHONE, status="approved", opted_out=True)

    by_sms, _ = await sms._resolve_sender(db, RELATIVE_PHONE)
    by_email, _, _ = await email_inbound._resolve_sender(db, RELATIVE_EMAIL)

    assert by_sms == "opted_out"
    assert by_email == "family"


@pytest.mark.asyncio
async def test_the_owner_resolves_from_config_on_either_channel(db, mocker):
    """A deployment that never set PRINCIPALS_JSON must still recognise her."""
    from app.services import access

    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    mocker.patch.object(settings, "owner_email", "cordia@example.com")

    assert (await access.resolve(db, phone="+16157080002")).role == "owner"
    assert (await access.resolve(db, email="cordia@example.com")).role == "owner"


@pytest.mark.asyncio
async def test_a_stranger_is_unknown_on_either_channel(db):
    from app.services import access

    assert (await access.resolve(db, phone="+17876765645")).role == "unknown"
    assert (await access.resolve(db, email="stranger@example.com")).role == "unknown"
