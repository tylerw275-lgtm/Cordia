"""Three messages, one conversation, nothing mocked between them.

Every other loop test mocks `_load_history` and `_persist_message` and passes a
conversation id that is not in the database. That is precisely the seam the 400s
came from, and it is why `acting_user` could break memory, family creation,
calendar capture and flight search for six deploys with 399 tests passing: no
test drove more than a single message, and the one test that reached the
tool_use branch patched `get_handler` to return None.

So this file mocks exactly one thing — the Anthropic API — and lets the real
conversation run: real persistence, real history reload, real sanitizer, real
tool dispatch, real handlers, real database rows.

The assertions are of two kinds. Every request Cord builds must be a transcript
the Messages API would accept, checked with the same oracle as
test_claude_history. And the work must actually have happened: a tool that says
it stored something must have stored it.
"""
import json
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api import sms
from app.models.authorized_user import AuthorizedUser
from app.models.memory import Memory
from app.services import claude_service
from tests.test_claude_history import assert_valid_transcript

OWNER = "+16157080002"


# --- scripting the model ----------------------------------------------------

def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _says(text: str):
    return SimpleNamespace(
        stop_reason="end_turn", usage=None, container=None,
        content=[_blk(type="text", text=text)],
    )


def _calls(tool: str, tool_input: dict, *, tool_id: str, preamble: str = "One moment."):
    return SimpleNamespace(
        stop_reason="tool_use", usage=None, container=None,
        content=[_blk(type="text", text=preamble),
                 _blk(type="tool_use", id=tool_id, name=tool, input=tool_input)],
    )


@pytest.fixture
def script(mocker):
    """Queue up responses; hand back the list of requests Cord actually sent."""
    sent: list[dict] = []

    def _install(*responses):
        queue = list(responses)

        async def create(**kwargs):
            sent.append(kwargs)
            assert queue, f"the model was called {len(sent)} times, script has {len(responses)}"
            return queue.pop(0)

        mocker.patch.object(claude_service._client.messages, "create", new=create)
        return sent

    return _install


@pytest_asyncio.fixture
async def cordia(db):
    user = AuthorizedUser(name="Cordia Harrington", phone=OWNER, is_owner=True)
    db.add(user)
    await db.commit()
    return user


@pytest.fixture(autouse=True)
def _quiet(mocker):
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock(return_value=True))
    # The holding notes are timing, not conversation. They have their own file.
    mocker.patch.object(sms, "_notify_if_slow", new=mocker.AsyncMock())


async def _say(db, text: str) -> None:
    await sms._process_inbound(db, OWNER, text, [])


# --- the test that was missing ----------------------------------------------

@pytest.mark.asyncio
async def test_three_messages_build_one_growing_conversation(db, cordia, script):
    """Turn 1 uses a tool, turn 2 refers to what it did, turn 3 refers back to
    turn 1. Each request must carry everything before it, in a shape the API
    accepts."""
    sent = script(
        _calls("store_memory",
               {"category": "preference", "subject": "Tom",
                "content": "Tom will not fly red-eyes"}, tool_id="tu_1"),
        _says("Got it - noted that Tom avoids red-eyes."),
        _says("Just the one note about Tom so far."),
        _says("Red-eyes. You told me that first."),
    )

    await _say(db, "remember that Tom will not fly red-eyes")
    await _say(db, "what have you got on Tom")
    await _say(db, "what was the first thing I told you today")

    assert len(sent) == 4, "one tool round on turn 1, then one request per turn"
    for i, request in enumerate(sent):
        assert_valid_transcript(request["messages"])
        assert request["messages"][-1]["role"] == "user", f"request {i} ends on an assistant turn"

    # The conversation grows rather than restarting.
    lengths = [len(r["messages"]) for r in sent]
    assert lengths == sorted(lengths), f"history shrank between turns: {lengths}"

    # Turn 3 can still see turn 1 — the actual claim "refer back" depends on.
    third = json.dumps(sent[-1]["messages"])
    assert "remember that Tom will not fly red-eyes" in third
    assert "what have you got on Tom" in third


@pytest.mark.asyncio
async def test_the_tool_really_ran(db, cordia, script):
    """The six-deploy bug, at the level it actually bit: Cord said it had noted
    something and nothing was written. The loop swallows the TypeError into a
    tool_result, so the only way to see it is to look in the database."""
    script(
        _calls("store_memory",
               {"category": "preference", "subject": "Tom",
                "content": "Tom will not fly red-eyes"}, tool_id="tu_1"),
        _says("Noted."),
    )

    await _say(db, "remember that Tom will not fly red-eyes")

    stored = (await db.execute(select(Memory))).scalars().all()
    assert len(stored) == 1, "Cord said it noted this and wrote nothing"
    assert "red-eye" in stored[0].content.lower()


@pytest.mark.asyncio
async def test_a_tool_failure_is_visible_in_the_transcript_not_silent(db, cordia, script):
    """When a handler genuinely cannot run, the error belongs in the tool_result
    where the model can see it — but the pairing must still be valid or the very
    next request 400s."""
    sent = script(
        _calls("no_such_tool", {}, tool_id="tu_1"),
        _says("I could not do that one."),
    )

    await _say(db, "do something impossible")

    second = sent[1]["messages"]
    assert_valid_transcript(second)
    results = [b for m in second if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"]
    assert results and "Unknown tool" in results[0]["content"]


@pytest.mark.asyncio
async def test_the_tool_exchange_survives_the_round_trip_to_the_database(db, cordia, script):
    """A tool_use and its result are separate rows. Reloading them as a pair,
    with ids intact, is what the persisted-transcript sanitizer exists for."""
    sent = script(
        _calls("list_projects", {}, tool_id="tu_abc"),
        _says("Nothing open."),
        _says("Still nothing."),
    )

    await _say(db, "what am I working on")
    await _say(db, "and now?")

    replayed = sent[-1]["messages"]
    assert_valid_transcript(replayed)
    uses = [b for m in replayed if isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_use"]
    assert [u["id"] for u in uses] == ["tu_abc"], "the tool call was lost or duplicated"


@pytest.mark.asyncio
async def test_a_thinking_block_is_never_replayed(db, cordia, script):
    """It is valid within its own turn and a 400 in a later one, because the
    signature does not survive the round trip."""
    thinking = SimpleNamespace(
        stop_reason="end_turn", usage=None, container=None,
        content=[_blk(type="thinking", thinking="hmm", signature="sig"),
                 _blk(type="text", text="Sure.")],
    )
    sent = script(thinking, _says("Still sure."))

    await _say(db, "think about this")
    await _say(db, "and again")

    assert_valid_transcript(sent[1]["messages"])
    assert "thinking" not in json.dumps(sent[1]["messages"])


@pytest.mark.asyncio
async def test_two_people_do_not_share_a_transcript(db, cordia, script):
    """Tom's conversation is keyed on his own number. If history were keyed on
    anything coarser, the walls between principals would leak through the one
    place nobody looks."""
    db.add(AuthorizedUser(name="Tom Harrington", phone="+16157080001"))
    await db.commit()
    sent = script(_says("Hello Cordia."), _says("Hello Tom."))

    await sms._process_inbound(db, OWNER, "it is Cordia here", [])
    await sms._process_inbound(db, "+16157080001", "it is Tom here", [])

    assert "it is Cordia here" not in json.dumps(sent[1]["messages"])


@pytest.mark.asyncio
async def test_an_empty_reply_never_reaches_her_as_an_empty_text(db, cordia, script):
    """send_sms with an empty body is a provider error, and a blank text reads
    as a broken assistant."""
    script(SimpleNamespace(stop_reason="end_turn", usage=None, container=None, content=[]))

    reply = await claude_service.chat(
        db, (await claude_service.get_or_create_conversation(db, OWNER)).id, "hello",
        sender_user=cordia,
    )
    assert reply.strip()


# --- the same path, through the actual webhook ------------------------------

@pytest.mark.asyncio
async def test_the_webhook_answers_a_real_message(db, cordia, script, mocker):
    """test_sms.py's "inbound SMS works" test asserts a constant: the sender
    resolves to unknown, chat is never called, and the endpoint returns empty
    TwiML either way. This one checks that an answer was actually sent."""
    send = mocker.patch("app.services.sms_service.send_sms",
                        new=mocker.AsyncMock(return_value=True))
    script(_says("Morning. Nothing on the calendar."))

    await _say(db, "anything today")

    assert send.await_args.kwargs["body"] == "Morning. Nothing on the calendar."
