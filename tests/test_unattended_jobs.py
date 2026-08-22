"""The jobs that run with nobody watching.

Five of these fire on a schedule and text Cordia without anyone asking. They had
no tests at all. That matters more than it does for a webhook, because there is
no one to notice: a job that crashes on the second of fifty family members just
stops, and the only symptom is a text that never arrives.

Three properties are checked for every one of them.

They text Cordia and nobody else — these run on their own, and a proactive
message to a relative would break the carrier rule that Cord never messages
first. They record only what actually went out, because writing an unsent
message into her transcript makes Cord answer as though she had read it. And
one bad row does not end the run.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.family import FamilyMember
from app.scheduler.jobs import birthday_prep, email_poll, flight_monitor, morning_brief
from app.services.usage_service import sms_segments

OWNER = "+16157080002"


@pytest.fixture(autouse=True)
def _owner(mocker):
    mocker.patch.object(settings, "cordia_phone_number", OWNER)
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())


@pytest.fixture
def sent(mocker):
    return mocker.patch("app.services.sms_service.send_sms",
                        new=mocker.AsyncMock(return_value=True))


@pytest.fixture
def session(mocker, db):
    """Point the jobs' own get_db_session at the test session."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake():
        yield db

    for module in (birthday_prep, flight_monitor, morning_brief, email_poll):
        if hasattr(module, "get_db_session"):
            mocker.patch.object(module, "get_db_session", _fake)
    return db


# --- they only ever text Cordia ---------------------------------------------

@pytest.mark.asyncio
async def test_the_morning_brief_goes_only_to_cordia(session, sent, db):
    db.add(FamilyMember(name="Bea Harrington", relationship="granddaughter",
                        birthday=date(2015, date.today().month, date.today().day),
                        phone="+16155551212"))
    await db.commit()

    await morning_brief.send_morning_brief()

    assert sent.await_count == 1
    assert sent.await_args.kwargs["to"] == OWNER


@pytest.mark.asyncio
async def test_birthday_prep_goes_only_to_cordia(session, sent, db, mocker):
    lead = settings.birthday_prep_lead_days
    target = date.today() + timedelta(days=lead)
    db.add(FamilyMember(name="Bea Harrington", relationship="granddaughter",
                        birthday=date(2015, target.month, target.day),
                        phone="+16155551212"))
    await db.commit()
    mocker.patch.object(birthday_prep, "compose_birthday_prep",
                        new=mocker.AsyncMock(return_value="Bea's birthday is in a week."))

    await birthday_prep.send_birthday_prep()

    assert sent.await_count == 1
    assert sent.await_args.kwargs["to"] == OWNER


# --- nothing to say means nothing sent --------------------------------------

@pytest.mark.asyncio
async def test_a_quiet_day_gets_no_morning_text(session, sent):
    """A daily "nothing to report" is a daily charge and a daily interruption."""
    await morning_brief.send_morning_brief()
    assert not sent.called


@pytest.mark.asyncio
async def test_no_brief_without_a_configured_number(session, sent, mocker):
    mocker.patch.object(settings, "cordia_phone_number", "")
    await morning_brief.send_morning_brief()
    assert not sent.called


# --- what is recorded is what she actually received -------------------------

@pytest.mark.asyncio
async def test_a_suppressed_brief_is_not_written_into_her_transcript(session, mocker, db):
    """send_sms returns False after STOP. Recording it anyway would leave Cord
    answering follow-ups to a text she never got."""
    db.add(FamilyMember(name="Bea Harrington", relationship="granddaughter",
                        birthday=date(2015, date.today().month, date.today().day)))
    await db.commit()
    mocker.patch("app.services.sms_service.send_sms",
                 new=mocker.AsyncMock(return_value=False))
    record = mocker.patch("app.services.claude_service.record_assistant_message",
                          new=mocker.AsyncMock())

    await morning_brief.send_morning_brief()
    assert not record.called


@pytest.mark.asyncio
async def test_a_suppressed_birthday_note_is_not_recorded_either(session, mocker, db):
    """This one did record it — the only job of the five that didn't check."""
    lead = settings.birthday_prep_lead_days
    target = date.today() + timedelta(days=lead)
    db.add(FamilyMember(name="Bea Harrington", relationship="granddaughter",
                        birthday=date(2015, target.month, target.day)))
    await db.commit()
    mocker.patch.object(birthday_prep, "compose_birthday_prep",
                        new=mocker.AsyncMock(return_value="Bea's birthday is in a week."))
    mocker.patch("app.services.sms_service.send_sms",
                 new=mocker.AsyncMock(return_value=False))
    record = mocker.patch("app.services.claude_service.record_assistant_message",
                          new=mocker.AsyncMock())

    await birthday_prep.send_birthday_prep()
    assert not record.called


# --- one bad row does not end the run ---------------------------------------

@pytest.mark.asyncio
async def test_a_broken_watch_does_not_stop_the_others(session, sent, db, mocker):
    """Nobody is watching. A run that dies on the first of ten watches looks
    exactly like a run where no price moved."""
    from app.models.trips import FlightWatch

    for i, (origin, dest) in enumerate([("BNA", "LGA"), ("BNA", "NBO")]):
        db.add(FlightWatch(origin=origin, destination=dest,
                           depart_date=date(2027, 6, 1), num_adults=1,
                           cabin_class="economy", is_active=True, target_price=100))
    await db.commit()

    calls = []

    async def search(**kw):
        calls.append(kw["destination"])
        if kw["destination"] == "LGA":
            raise RuntimeError("Duffel is down")
        return [{"price": 50.0, "currency": "USD", "carrier": "QR"}]

    mocker.patch("app.services.duffel_service.search_flights", new=search)

    await flight_monitor.monitor_flight_prices()

    assert set(calls) == {"LGA", "NBO"}, "the run stopped at the first failure"
    assert sent.await_count == 1


@pytest.mark.asyncio
async def test_one_unreadable_email_does_not_abandon_the_rest(mocker):
    """And the failed one stays unread so the next poll retries it."""
    mocker.patch.object(settings, "email_address", "cord@example.com")
    mocker.patch.object(settings, "email_app_password", "secret")
    mocker.patch.object(email_poll.asyncio, "to_thread", new=mocker.AsyncMock(side_effect=[
        [("1", "a@example.com", "hi", "one"),
         ("2", "b@example.com", "hi", "two"),
         ("3", "c@example.com", "hi", "three")],
        None,
    ]))

    seen = []

    async def process(db, sender, subject, body):
        seen.append(sender)
        if sender == "b@example.com":
            raise RuntimeError("bad encoding")
        return "replied_as_owner"

    mocker.patch("app.services.email_inbound.process_inbound_email", new=process)
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake():
        yield None

    mocker.patch.object(email_poll, "get_db_session", _fake)

    await email_poll.poll_inbound_email()

    assert seen == ["a@example.com", "b@example.com", "c@example.com"]
    marked = email_poll.asyncio.to_thread.await_args.args
    assert marked[1] == ["1", "3"], "the failed message was marked read and lost"


# --- what these messages cost -----------------------------------------------

@pytest.mark.asyncio
async def test_a_flight_alert_is_not_billed_as_unicode(session, sent, db, mocker):
    """It contained an arrow, which alone re-encodes the whole message as UCS-2
    and drops the segment limit from 160 characters to 70."""
    from app.models.trips import FlightWatch

    db.add(FlightWatch(origin="BNA", destination="NBO", depart_date=date(2027, 6, 1),
                       num_adults=1, cabin_class="economy", is_active=True, target_price=2000))
    await db.commit()
    mocker.patch("app.services.duffel_service.search_flights",
                 new=mocker.AsyncMock(return_value=[
                     {"price": 1480.0, "currency": "USD", "carrier": "Qatar Airways"}]))

    await flight_monitor.monitor_flight_prices()

    from app.services.gsm import to_gsm
    body = to_gsm(sent.await_args.kwargs["body"])
    assert body.isascii(), f"non-ASCII survived into a billed message: {body!r}"
    assert sms_segments(body) == 1


def test_the_naples_capture_note_has_no_emoji():
    """It went out on every capture, and one emoji doubles the cost of the
    whole note."""
    import inspect

    from app.scheduler.jobs import naples_poll

    source = inspect.getsource(naples_poll)
    note_lines = [l for l in source.splitlines() if "note = f" in l]
    assert note_lines
    for line in note_lines:
        assert line.isascii(), line.strip()


# --- the nightly condense ----------------------------------------------------

@pytest.mark.asyncio
async def test_one_unsummarisable_conversation_does_not_stop_the_rest(db, mocker):
    """It runs with nobody watching, over every conversation there is."""
    import contextlib
    from datetime import datetime, timedelta, timezone

    from app.models.conversation import Conversation, Message
    from app.scheduler.jobs import history_condense
    from app.services import history_summary

    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    for key in ("+16157080002", "+16157080001", "+16153101552"):
        convo = Conversation(phone_number=key)
        db.add(convo)
        await db.commit()
        for i in range(8):
            db.add(Message(conversation_id=convo.id, role="user",
                           content=f"old {i}", created_at=long_ago + timedelta(minutes=i)))
        await db.commit()

    @contextlib.asynccontextmanager
    async def _fake():
        yield db

    mocker.patch.object(history_condense, "get_db_session", _fake)

    seen = []

    async def flaky(session, conversation, now=None):
        seen.append(conversation.phone_number)
        if conversation.phone_number == "+16157080001":
            raise RuntimeError("this one is broken")
        return True

    mocker.patch.object(history_summary, "summarise", new=flaky)

    # The job must survive a summarise() that raises, even though the real one
    # never does — a future edit could change that.
    try:
        await history_condense.condense_old_conversations()
    except RuntimeError:
        pytest.fail("one bad conversation stopped the whole nightly run")

    assert len(seen) == 3, f"stopped early: {seen}"


@pytest.mark.asyncio
async def test_nothing_old_enough_means_no_work_at_all(db, mocker):
    import contextlib

    from app.scheduler.jobs import history_condense
    from app.services import history_summary

    @contextlib.asynccontextmanager
    async def _fake():
        yield db

    mocker.patch.object(history_condense, "get_db_session", _fake)
    summarise = mocker.patch.object(history_summary, "summarise", new=mocker.AsyncMock())

    await history_condense.condense_old_conversations()
    assert not summarise.called
