"""Her St Thomas conversation, run end to end through the real code.

Every fix in this area came from a transcript, one failure at a time: the
promise with no later, the output truncation, the silent stop reason, the
first-keyword routing. None of that proves the pieces work *together*, which is
the only thing she will actually experience.

So this is the whole exchange, in her words, with only the model scripted:
the ask, the interview, her answers, sectioned research, delivery. Real
resolver, real history, real persistence, real tools.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.project import Project
from app.prompts import intent, playbooks
from app.services import claude_service

OWNER = "+16157080002"

HER_ASK = ("Plan a trip to st Thomas for me and my kids making arrangements for "
           "flights for everyone and taxi when we get there and packing list for "
           "everyone and their kids")

HER_ANSWERS = ("We have a timeshare that we're gonna stay at at the Ritz Carlton "
               "residence near cowpet Bay and will be going for a week. Tom and I "
               "will go down a few days before. All the kids will get their flights "
               "and send them to Karie. Everyone will get groceries when they get "
               "down there and mostly will be staying as the residence so we usually "
               "try and pack lite we usually use Kelley taxis to get to and from "
               "the airport")


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _says(text):
    return SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                           content=[_blk(type="text", text=text)])


def _calls(tool, tool_input, *, tool_id, text=""):
    content = ([_blk(type="text", text=text)] if text else []) + [
        _blk(type="tool_use", id=tool_id, name=tool, input=tool_input)]
    return SimpleNamespace(stop_reason="tool_use", usage=None, container=None,
                           content=content)


@pytest_asyncio.fixture
async def conversation(db):
    db.add(AuthorizedUser(name="Cordia Harrington", phone=OWNER, is_owner=True))
    await db.commit()
    return await claude_service.get_or_create_conversation(db, OWNER)


@pytest.fixture
def script(mocker):
    sent = []

    def _install(*responses):
        queue = list(responses)

        async def create(**kwargs):
            sent.append(kwargs)
            return queue.pop(0)

        mocker.patch.object(claude_service._client.messages, "create", new=create)
        return sent

    return _install


@pytest.fixture(autouse=True)
def _quiet(mocker):
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock(return_value=True))
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())
    mocker.patch.object(settings, "owner_email", "cordia@example.com")


# --- it reads the request correctly -----------------------------------------

def test_her_ask_is_understood_as_a_trip():
    """It routed to the outfitting-a-house playbook, on the word "packing" —
    so she was given a relocation adviser for a holiday."""
    assert intent.match(HER_ASK).name == "trip_planning"
    assert intent.is_deep_work(HER_ASK)


def test_the_taxi_part_is_a_booking_not_a_travel_musing():
    """"Kelley taxis to and from the airport" is a service to price and book."""
    assert intent.match("get me a taxi from the airport").name == "service_sourcing"
    assert playbooks.match("get me a taxi from the airport") == "service_sourcing"


def test_a_compound_ask_derives_its_own_interview():
    """No stock playbook covers flights AND a taxi AND packing for three
    families, and pretending one does is how she got the wrong questions."""
    assert playbooks.match(HER_ASK) is None


# --- the whole exchange -----------------------------------------------------

@pytest.mark.asyncio
async def test_the_trip_is_built_in_sections_and_actually_delivered(
        db, conversation, script, mocker):
    """The run that died. Ask, interview, her answers, research in sections,
    delivery — through the real resolver, history and tools.

    Sectioned because one response could not hold flights for three families
    plus a taxi plus packing lists: it truncated mid-tool-call and the email was
    never sent."""
    emailed = mocker.patch("app.services.email_service.send_email",
                           new=mocker.AsyncMock(return_value={"sent": True}))

    sent = script(
        # Turn one: open the project, ask only what changes the answer.
        _calls("start_project", {"title": "St Thomas trip", "request": HER_ASK},
               tool_id="tu_1"),
        _says("Just a few quick questions before I build the full plan:\n"
              "1. What dates, and how many nights?\n2. Is Tom coming too?\n"
              "3. Which families are joining?"),
        # Turn two: each section saved on its own, then one delivery.
        _calls("save_project_findings",
               {"project_id": "x", "findings": "Flights: Tom and Cordia Nov 22"},
               tool_id="tu_2"),
        _calls("save_project_findings",
               {"project_id": "x", "findings": "Taxi: Kelley Taxis, $40 each way"},
               tool_id="tu_3"),
        _calls("save_project_findings",
               {"project_id": "x", "findings": "Packing: light, laundry on site"},
               tool_id="tu_4"),
        _calls("send_report_email",
               {"subject": "St Thomas - flights, taxi and packing",
                "body_markdown": "## Flights\n...\n## Taxi\n...\n## Packing\n..."},
               tool_id="tu_5"),
        _says("Sent - flights, the Kelley taxi quote and packing lists are in "
              "your inbox."),
    )

    first = await claude_service.chat(db, conversation.id, HER_ASK)
    assert "questions" in first.lower(), "it answered without asking anything"

    project = (await db.execute(select(Project))).scalars().first()
    assert project is not None, "no project opened for a multi-part trip"

    reply = await claude_service.chat(db, conversation.id, HER_ANSWERS)

    assert emailed.called, "the plan was never sent"
    assert reply.startswith("Sent"), reply
    body = emailed.await_args.kwargs["body_markdown"]
    for section in ("Flights", "Taxi", "Packing"):
        assert section in body, f"{section} missing from the deliverable"
    assert len(sent) == 7, f"unexpected number of requests: {len(sent)}"


@pytest.mark.asyncio
async def test_both_turns_get_the_deep_budget(db, conversation, script):
    """Her answers are conversational — "we have a timeshare … we usually pack
    lite". The turn that does the actual work is the one after the interview, so
    it must not collapse to the short-reply budget."""
    from app.prompts.prompt_profiles import get_profile

    deep = get_profile(settings.claude_model).deep_max_tokens
    sent = script(_says("Questions?"), _says("Done."))

    await claude_service.chat(db, conversation.id, HER_ASK)
    await claude_service.chat(db, conversation.id, HER_ANSWERS)

    assert [r["max_tokens"] for r in sent] == [deep, deep]


@pytest.mark.asyncio
async def test_nothing_promises_a_delivery_it_does_not_make(
        db, conversation, script, mocker):
    """The line that ended her conversation: "let me finish building it and get
    it to you right now", then silence."""
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    sent = script(
        _says("Sorry about that - let me finish building it and get it to you right now."),
        _calls("send_report_email", {"subject": "St Thomas", "body_markdown": "## Flights"},
               tool_id="tu_1"),
        _says("Sent - it is in your inbox."),
    )

    reply = await claude_service.chat(db, conversation.id, "Didn't get it")

    assert len(sent) == 3, "the promise was accepted and the turn ended"
    assert "Sent" in reply


@pytest.mark.asyncio
async def test_a_deliverable_too_big_for_one_response_is_not_lost(
        db, conversation, script, mocker):
    """What actually happened: truncated mid-tool-call, so the email never ran
    and she was told to ask again."""
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    script(
        SimpleNamespace(stop_reason="max_tokens", usage=None, container=None,
                        content=[_blk(type="text", text="## Flights\n\nDelta 1422")]),
        _calls("save_project_findings", {"project_id": "x", "findings": "Flights..."},
               tool_id="tu_1"),
        _says("Flights saved; taxi and packing next."),
    )

    reply = await claude_service.chat(db, conversation.id, HER_ASK)

    assert reply != claude_service._FALLBACK_REPLY
    assert "ask me again if you don't hear back" not in reply


# --- the reply she reads on her phone ---------------------------------------

@pytest.mark.asyncio
async def test_the_text_summary_is_a_text_not_a_document(db, conversation, script, mocker):
    """A long deliverable goes to email; the SMS is the headline. Sending the
    document by text would bill as forty segments."""
    from app.services.gsm import to_gsm
    from app.services.usage_service import sms_segments

    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    summary = ("Sent - flights for all three families, the Kelley taxi quote both "
               "ways, and packing lists are in your inbox. Tom's fare needs "
               "booking by Friday.")
    script(_calls("send_report_email", {"subject": "St Thomas", "body_markdown": "#" * 5000},
                  tool_id="tu_1"),
           _says(summary))

    reply = await claude_service.chat(db, conversation.id, HER_ANSWERS)

    assert sms_segments(to_gsm(reply)) <= 2, "the summary itself is a multi-segment wall"


def test_the_sms_channel_asks_for_short_replies():
    from app.prompts.system_prompt import build_system_prompt

    sms = " ".join(b["text"] for b in build_system_prompt(None, channel="sms"))
    email = " ".join(b["text"] for b in build_system_prompt(None, channel="email"))
    assert "Keep replies SHORT" in sms
    assert "Length limits do not apply" in email


@pytest.mark.asyncio
async def test_with_no_inbox_configured_it_says_so_rather_than_pretending(
        db, conversation, mocker):
    """send_report_email returns early when OWNER_EMAIL is unset. The model has
    to be told that plainly, or it reports a delivery that never happened."""
    from app.tools import email_tools

    mocker.patch.object(settings, "owner_email", "")
    sent = mocker.patch("app.services.email_service.send_email", new=mocker.AsyncMock())

    result = await email_tools.send_report_email_handler(
        db, subject="St Thomas", body_markdown="## Flights")

    assert result["sent"] is False
    assert "No email on file" in result["message"]
    assert not sent.called
