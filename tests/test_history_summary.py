"""Condensing a conversation once it is a week old.

History is a window, and a trip planned across a year outlives any window
however wide. Past a point, replaying every message costs more than it is worth
and still loses the beginning.

Most of this file is about the ways it can go wrong, because a summary that
loses something means Cord confidently recalls a condensed version of a thing
that did not happen — worse than forgetting. Two properties matter more than
the condensing itself: the original messages are never deleted, and the
watermark never moves ahead of a stored summary.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.config import settings
from app.models.conversation import Conversation, Message
from app.services import claude_service, history_summary

OWNER = "+16157080002"
LONG_AGO = datetime.now(timezone.utc) - timedelta(days=30)
YESTERDAY = datetime.now(timezone.utc) - timedelta(days=1)


def _summary_response(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=None,
    )


@pytest_asyncio.fixture
async def conversation(db):
    convo = Conversation(phone_number=OWNER)
    db.add(convo)
    await db.commit()
    return convo


async def _add(db, convo, role, content, when):
    msg = Message(conversation_id=convo.id, role=role, content=content, created_at=when)
    db.add(msg)
    await db.commit()
    return msg


async def _old_conversation(db, convo, n=8, when=LONG_AGO):
    for i in range(n):
        await _add(db, convo, "user", f"old question {i}", when + timedelta(minutes=i))
        await _add(db, convo, "assistant",
                   json.dumps([{"type": "text", "text": f"old answer {i}"}]),
                   when + timedelta(minutes=i, seconds=30))


# --- it condenses, and stops replaying what it condensed ---------------------

@pytest.mark.asyncio
async def test_old_messages_stop_being_replayed(db, conversation, mocker):
    await _old_conversation(db, conversation)
    await _add(db, conversation, "user", "something recent", YESTERDAY)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(return_value=_summary_response(
                            "Booked the 5pm. Tom will not fly red-eyes.")))

    assert await history_summary.summarise(db, conversation) is True

    history = await claude_service._load_history(
        db, conversation.id, since=conversation.summary_through)
    replayed = json.dumps(history)
    assert "old question" not in replayed
    assert "something recent" in replayed


@pytest.mark.asyncio
async def test_the_original_messages_are_still_there(db, conversation, mocker):
    """The whole safety of this design. A summary that lost something can be
    rebuilt from the rows; deleted history cannot."""
    await _old_conversation(db, conversation)
    before = (await db.execute(select(func.count(Message.id)))).scalar()
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(return_value=_summary_response("notes")))

    await history_summary.summarise(db, conversation)

    assert (await db.execute(select(func.count(Message.id)))).scalar() == before


@pytest.mark.asyncio
async def test_the_summary_is_stored_and_watermarked(db, conversation, mocker):
    await _old_conversation(db, conversation)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(return_value=_summary_response(
                            "Decided on Gabriel Kreuther, 5pm, Aug 25.")))

    await history_summary.summarise(db, conversation)

    assert "Gabriel Kreuther" in conversation.summary
    assert conversation.summary_through is not None
    assert conversation.summarised_at is not None


# --- failure must be safe ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_call_leaves_the_history_alone(db, conversation, mocker):
    """The dangerous failure: a watermark that moved without a summary would
    silently drop exactly the messages this exists to preserve."""
    await _old_conversation(db, conversation)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(side_effect=RuntimeError("API down")))

    assert await history_summary.summarise(db, conversation) is False
    assert conversation.summary_through is None
    assert conversation.summary is None

    history = await claude_service._load_history(
        db, conversation.id, since=conversation.summary_through)
    assert "old question 0" in json.dumps(history), "history was lost with no summary"


@pytest.mark.asyncio
async def test_an_empty_summary_is_refused(db, conversation, mocker):
    """A model that returns nothing must not be treated as 'nothing happened'."""
    await _old_conversation(db, conversation)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(return_value=_summary_response("   ")))

    assert await history_summary.summarise(db, conversation) is False
    assert conversation.summary_through is None


@pytest.mark.asyncio
async def test_summarising_never_raises(db, conversation, mocker):
    """It runs unattended, and one bad conversation must not stop the rest."""
    await _old_conversation(db, conversation)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(side_effect=Exception("anything at all")))
    assert await history_summary.summarise(db, conversation) is False


# --- what it leaves alone ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_recent_conversation_is_not_touched(db, conversation, mocker):
    """No model call at all — this runs over every conversation nightly."""
    for i in range(10):
        await _add(db, conversation, "user", f"recent {i}", YESTERDAY)
    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock())

    assert await history_summary.due(db) == []
    assert await history_summary.summarise(db, conversation) is False
    assert not create.called


@pytest.mark.asyncio
async def test_a_short_old_conversation_is_not_worth_a_call(db, conversation, mocker):
    await _add(db, conversation, "user", "hello", LONG_AGO)
    await _add(db, conversation, "assistant", "hi", LONG_AGO)
    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock())

    assert await history_summary.due(db) == []
    assert not create.called


@pytest.mark.asyncio
async def test_a_conversation_with_enough_old_messages_is_due(db, conversation):
    await _old_conversation(db, conversation)
    assert [c.id for c in await history_summary.due(db)] == [conversation.id]


# --- the transcript stays valid across the cut ------------------------------

@pytest.mark.asyncio
async def test_a_tool_exchange_split_by_the_cut_still_produces_a_valid_transcript(
        db, conversation, mocker):
    """The cut is on a timestamp, so it can land between a tool_use and its
    result — which is exactly the shape that produced 400s before."""
    from tests.test_claude_history import assert_valid_transcript

    await _add(db, conversation, "user", "what am I working on", LONG_AGO)
    await _add(db, conversation, "assistant", json.dumps([
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "tu_1", "name": "list_projects", "input": {}},
    ]), LONG_AGO + timedelta(seconds=1))
    # The result lands AFTER the watermark: its tool_use is now unreplayed.
    await _add(db, conversation, "tool", json.dumps([
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"},
    ]), YESTERDAY)
    await _add(db, conversation, "user", "and now?", YESTERDAY + timedelta(seconds=1))

    history = await claude_service._load_history(
        db, conversation.id, since=LONG_AGO + timedelta(seconds=30))

    assert_valid_transcript(history)


# --- it reaches the model ----------------------------------------------------

def test_the_summary_becomes_a_prompt_block():
    convo = Conversation(phone_number=OWNER, summary="Booked the 5pm sitting.")
    block = history_summary.for_prompt(convo)
    assert "WHAT CAME BEFORE" in block
    assert "Booked the 5pm sitting." in block


def test_no_summary_means_no_block():
    assert history_summary.for_prompt(Conversation(phone_number=OWNER)) == ""
    assert history_summary.for_prompt(None) == ""


def test_the_block_tells_the_model_to_answer_from_it():
    """Otherwise it reads as background and Cord says it does not recall."""
    convo = Conversation(phone_number=OWNER, summary="x")
    assert "rather than saying you do not recall" in history_summary.for_prompt(convo)


@pytest.mark.asyncio
async def test_the_summary_reaches_the_system_prompt_on_a_real_turn(db, conversation, mocker):
    conversation.summary = "Tom will not fly red-eyes."
    conversation.summary_through = LONG_AGO
    await db.commit()

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", usage=None, container=None,
            content=[SimpleNamespace(type="text", text="Noted.",
                                     model_dump=lambda: {"type": "text", "text": "Noted."})],
        )

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    await claude_service.chat(db, conversation.id, "what do you know about Tom")

    system = " ".join(b["text"] for b in sent[0]["system"])
    assert "Tom will not fly red-eyes." in system


# --- it must not become the bloat it removes --------------------------------

@pytest.mark.asyncio
async def test_the_summary_is_bounded(db, conversation, mocker):
    """It rides in every request."""
    await _old_conversation(db, conversation)
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(return_value=_summary_response("x" * 50_000)))

    await history_summary.summarise(db, conversation)

    assert len(conversation.summary) <= settings.history_summary_max_chars
    assert settings.history_summary_max_chars < settings.history_max_chars


@pytest.mark.asyncio
async def test_the_previous_summary_is_given_back_so_nothing_is_dropped(
        db, conversation, mocker):
    """A second pass must build on the first, not replace it."""
    conversation.summary = "Earlier: chose Gabriel Kreuther."
    conversation.summary_through = LONG_AGO - timedelta(days=1)
    await db.commit()
    await _old_conversation(db, conversation)

    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock(return_value=_summary_response("merged")))

    await history_summary.summarise(db, conversation)

    prompt = create.await_args.kwargs["messages"][0]["content"]
    assert "Earlier: chose Gabriel Kreuther." in prompt


# --- what the summariser is actually shown ----------------------------------

@pytest.mark.asyncio
async def test_tool_plumbing_is_not_offered_as_conversation(db, conversation, mocker):
    await _old_conversation(db, conversation)
    await _add(db, conversation, "tool", json.dumps(
        [{"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"}]), LONG_AGO)
    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock(return_value=_summary_response("notes")))

    await history_summary.summarise(db, conversation)

    prompt = create.await_args.kwargs["messages"][0]["content"]
    assert "tool_result" not in prompt


@pytest.mark.asyncio
async def test_an_assistant_turn_is_shown_as_its_words_not_its_json(db, conversation, mocker):
    await _old_conversation(db, conversation)
    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock(return_value=_summary_response("notes")))

    await history_summary.summarise(db, conversation)

    prompt = create.await_args.kwargs["messages"][0]["content"]
    assert "old answer 0" in prompt
    assert '"type": "text"' not in prompt


def test_the_instructions_say_what_to_keep_and_what_to_drop():
    """The judgment is the feature, so it is spelled out rather than left to
    'summarise this'."""
    prompt = history_summary._PROMPT
    for kept in ("Decisions", "Commitments", "Never invent", "KEEP it"):
        assert kept in prompt
    for dropped in ("Greetings", "holding notes", "superseded"):
        assert dropped in prompt


@pytest.mark.asyncio
async def test_a_conversation_of_pure_plumbing_advances_rather_than_looping(
        db, conversation, mocker):
    """Otherwise the nightly job looks at the same rows forever."""
    for i in range(8):
        await _add(db, conversation, "tool", json.dumps(
            [{"type": "tool_result", "tool_use_id": f"tu_{i}", "content": "{}"}]),
            LONG_AGO + timedelta(minutes=i))
    create = mocker.patch.object(claude_service._client.messages, "create",
                                 new=mocker.AsyncMock())

    await history_summary.summarise(db, conversation)

    assert not create.called, "spent a model call on nothing but plumbing"
    assert conversation.summary_through is not None
