"""Carrier keywords must not eat ordinary conversation.

Cord asked "Want me to email you the full organized list?", Cordia replied
"yes", and got the subscription boilerplate back. "YES" was a re-subscription
keyword checked before the sender was even resolved, so the most common reply in
English never reached the assistant. Every yes/no question Cord asked was a trap.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

OWNER = "+16157080002"


@pytest.fixture(autouse=True)
def _owner(mocker):
    from app.config import settings
    mocker.patch.object(settings, "cordia_test_phone_number", OWNER)
    mocker.patch.object(settings, "principals_json", "")


async def _consent(db, phone, opted_out=False):
    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status, opted_out_at) "
             "VALUES (:p, :ts, 'web_form', 'approved', :out) "
             "ON CONFLICT (phone) DO UPDATE SET opted_out_at = EXCLUDED.opted_out_at, "
             "approval_status = 'approved'"),
        {"p": phone, "ts": datetime.now(timezone.utc),
         "out": datetime.now(timezone.utc) if opted_out else None},
    )
    await db.commit()


@pytest.fixture
def wired(mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    chat = mocker.patch("app.services.claude_service.chat", return_value="Sent it over.")
    return send, chat


async def _text_in(client, body, sid):
    return await client.post("/webhook/sms", data={"From": OWNER, "Body": body, "MessageSid": sid})


# --- the bug ---------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("case,reply", [
    ("lower", "yes"), ("title", "Yes"), ("upper", "YES"), ("padded", " yes "),
])
async def test_an_active_subscriber_saying_yes_reaches_the_assistant(db, client, wired, case, reply):
    send, chat = wired
    await _consent(db, OWNER)

    # Distinct ids: two cases that strip to the same text would otherwise be
    # dropped as a provider retry of one message.
    await _text_in(client, reply, f"SM-yes-{case}")

    assert chat.called, f"{reply!r} never reached the assistant"
    bodies = [c.kwargs.get("body", "") for c in send.await_args_list]
    assert not any("You're subscribed" in b for b in bodies), bodies


@pytest.mark.asyncio
async def test_an_active_subscriber_saying_start_is_not_re_subscribed(db, client, wired):
    """"start on the naples list" is a bare START away from the same trap."""
    send, chat = wired
    await _consent(db, OWNER)

    await _text_in(client, "start", "SM-kw-start")

    assert chat.called
    assert not any("You're subscribed" in c.kwargs.get("body", "")
                   for c in send.await_args_list)


# --- what must still work --------------------------------------------------

@pytest.mark.asyncio
async def test_an_opted_out_number_texting_yes_still_re_subscribes(db, client, wired):
    """The compliance half. Gating on consent must not take this away."""
    send, chat = wired
    await _consent(db, OWNER, opted_out=True)

    await _text_in(client, "YES", "SM-kw-resub")

    assert any("You're subscribed" in c.kwargs.get("body", "")
               for c in send.await_args_list)
    assert not chat.called


@pytest.mark.asyncio
async def test_a_brand_new_number_texting_start_subscribes(db, client, wired):
    send, chat = wired

    await _text_in(client, "START", "SM-kw-new")

    assert any("You're subscribed" in c.kwargs.get("body", "")
               for c in send.await_args_list)


@pytest.mark.asyncio
async def test_stop_still_works_mid_conversation(db, client, wired):
    """Carrier-mandated in every state, subscribed or not."""
    send, chat = wired
    await _consent(db, OWNER)

    await _text_in(client, "STOP", "SM-kw-stop")

    from app.services import consent_service
    assert await consent_service.status_for(db, OWNER) == "opted_out"
    assert send.called and not chat.called


@pytest.mark.asyncio
async def test_help_still_answers(db, client, wired):
    send, chat = wired
    await _consent(db, OWNER)

    await _text_in(client, "HELP", "SM-kw-help")

    assert send.called and not chat.called


@pytest.mark.asyncio
async def test_help_inside_a_sentence_reaches_the_assistant(db, client, wired):
    """Only a bare HELP is the keyword — "help me plan a party" is a request."""
    send, chat = wired
    await _consent(db, OWNER)

    await _text_in(client, "help me plan a party", "SM-kw-helpme")

    assert chat.called
