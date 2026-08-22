"""Not re-billing the transcript on every round.

One cache breakpoint existed, on the last system block — which covers the tool
schemas and the system prompt and nothing after it. Every replayed message was
charged at full rate on every request.

The monthly saving is modest. The saving inside a turn is not: a deep research
turn runs up to 25 tool rounds and re-sends the whole transcript on each one, so
without a breakpoint it pays for the same history twenty-five times.

The failure mode is silence — a breakpoint that never hits looks exactly like
one that does — so most of this is about the ways it can quietly not work.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.authorized_user import AuthorizedUser
from app.services import claude_service
from app.services.claude_service import _cache_history
from tests.test_claude_history import assert_valid_transcript

OWNER = "+16157080002"
LONG = "x" * 4_000


def _blocks(text=LONG):
    return [{"type": "text", "text": text}]


def _marks(turn) -> bool:
    content = turn["content"]
    return isinstance(content, list) and any("cache_control" in b for b in content)


def _count(messages) -> int:
    return sum(
        1 for m in messages if isinstance(m["content"], list)
        for b in m["content"] if "cache_control" in b
    )


# --- where the breakpoint goes ----------------------------------------------

def test_the_end_of_history_is_marked():
    history = [{"role": "user", "content": LONG},
               {"role": "assistant", "content": _blocks()}]
    out = _cache_history(history)
    assert _marks(out[-1])


def test_nothing_earlier_is_marked():
    """One breakpoint, not one per turn — there are only four per request."""
    history = [{"role": "user", "content": LONG},
               {"role": "assistant", "content": _blocks()},
               {"role": "user", "content": LONG}]
    assert _count(_cache_history(history)) == 1


@pytest.mark.asyncio
async def test_this_turns_message_is_never_marked(db, mocker):
    """The prefix has to be identical across requests to hit, and the new
    message is the part that changes."""
    db.add(AuthorizedUser(name="Cordia", phone=OWNER, is_owner=True))
    await db.commit()
    convo = await claude_service.get_or_create_conversation(db, OWNER)
    for i in range(6):
        await claude_service._persist_message(db, convo.id, "user", LONG)
        await claude_service._persist_message(
            db, convo.id, "assistant", [{"type": "text", "text": LONG}])

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", usage=None, container=None,
            content=[SimpleNamespace(type="text", text="ok",
                                     model_dump=lambda: {"type": "text", "text": "ok"})])

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    await claude_service.chat(db, convo.id, "and now?")

    messages = sent[0]["messages"]
    assert not _marks(messages[-1]), "the new message was inside the cached prefix"
    assert _count(messages) == 1


# --- the shapes history actually comes in -----------------------------------

def test_a_plain_text_turn_is_given_a_block_to_hold_the_marker():
    """_load_history yields strings as well as block lists, and cache_control
    attaches to a block."""
    out = _cache_history([{"role": "user", "content": "y" * 6_000}])
    assert out[0]["content"][0]["type"] == "text"
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_a_tool_result_turn_can_carry_it():
    history = [{"role": "user", "content": LONG},
               {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "tu_1", "content": LONG}]}]
    assert _marks(_cache_history(history)[-1])


def test_the_original_history_is_not_mutated():
    """A caller holding the list should not find a breakpoint appear in it."""
    history = [{"role": "user", "content": _blocks()}]
    _cache_history(history)
    assert "cache_control" not in history[0]["content"][0]


# --- when it would do nothing -----------------------------------------------

def test_a_short_history_is_left_alone():
    """Under ~1,024 tokens the API will not cache the prefix, so marking it
    just spends one of the four breakpoints."""
    history = [{"role": "user", "content": "hey"},
               {"role": "assistant", "content": _blocks("hi")}]
    assert _cache_history(history) == history


def test_no_history_is_safe():
    assert _cache_history([]) == []


def test_an_empty_content_list_is_not_marked():
    assert _count(_cache_history([{"role": "user", "content": []}])) == 0


# --- it must not break the transcript ---------------------------------------

def test_the_transcript_is_still_valid_with_the_marker():
    """The invariants here are real 400s, and this adds a key to a block that
    the API validates."""
    history = [
        {"role": "user", "content": LONG},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "tu_1", "name": "list_projects", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"}]},
        {"role": "assistant", "content": _blocks()},
    ]
    assert_valid_transcript(_cache_history(history))


def test_a_persisted_breakpoint_is_still_stripped_on_replay():
    """Ours is added on the way out. One restored from the database would put a
    marker at an arbitrary point in the transcript, so that stays stripped."""
    import json

    raw = json.dumps([{"type": "text", "text": "hello",
                       "cache_control": {"type": "ephemeral"}}])
    decoded = claude_service._decode_content(raw, "assistant")
    assert decoded == [{"type": "text", "text": "hello"}]


# --- the breakpoint budget --------------------------------------------------

def test_two_breakpoints_total_leaves_headroom():
    """Four per request. One on the system prompt, one here."""
    from app.prompts.system_prompt import build_system_prompt

    system = sum(1 for b in build_system_prompt(None) if "cache_control" in b)
    history = _count(_cache_history([{"role": "user", "content": LONG},
                                     {"role": "assistant", "content": _blocks()}]))
    assert system == 1
    assert history == 1
    assert system + history <= 4
