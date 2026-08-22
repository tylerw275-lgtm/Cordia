"""A field we never sent it came back and killed the turn.

She asked for the St Thomas plan again on the new model, and got:

    messages.58.content.1.text.parsed_output: Extra inputs are not permitted

Message 58 — a turn that was working. Opus 5 returns a `parsed_output` field on
text blocks. The SDK passes fields it does not know through untouched, so
`model_dump()` faithfully includes it, and the loop was feeding raw dumps
straight back into the request they came from. The API rejects its own output.

The rule is that anything going back into a request carries only what the API
accepts as input. And it has to hold for the *next* field they add, not just
this one, which is why unrecognised block types are kept whole rather than
matched against a list.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.authorized_user import AuthorizedUser
from app.services import claude_service
from app.services.claude_service import _for_this_turn

OWNER = "+16157080002"


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _says(text, **extra):
    return SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                           content=[_blk(type="text", text=text, **extra)])


def _calls(tool, tool_input=None, *, tool_id="tu_1", text="", **extra):
    content = ([_blk(type="text", text=text, **extra)] if text else []) + [
        _blk(type="tool_use", id=tool_id, name=tool, input=tool_input or {})]
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
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())


def _fields(messages, block_type):
    """Every key on every block of this type, across a request's messages."""
    keys = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == block_type:
                keys |= set(block)
    return keys


# --- the field that broke it -------------------------------------------------

def test_parsed_output_never_goes_back():
    """The exact rejection: a text block carrying the model's own extra field."""
    out = _for_this_turn([{"type": "text", "text": "Here are the flights",
                           "parsed_output": {"flights": []}, "citations": None}])

    assert out == [{"type": "text", "text": "Here are the flights"}]


def test_a_tool_use_block_carries_only_what_the_api_takes_back():
    out = _for_this_turn([{"type": "tool_use", "id": "tu_1", "name": "send_report_email",
                           "input": {"subject": "St Thomas"},
                           "cache_control": None, "something_new": "x"}])

    assert out == [{"type": "tool_use", "id": "tu_1", "name": "send_report_email",
                    "input": {"subject": "St Thomas"}}]


# --- and what must survive it ------------------------------------------------

def test_a_thinking_block_keeps_its_signature():
    """Within one turn a thinking block replays, and it is invalid without the
    signature. `_to_api_block` drops these on purpose; here that would be wrong."""
    out = _for_this_turn([{"type": "thinking", "thinking": "hmm", "signature": "sig123"}])

    assert out == [{"type": "thinking", "thinking": "hmm", "signature": "sig123"}]


def test_server_tool_blocks_round_trip_for_a_pause_resume():
    """A paused turn resumes by sending its own server-tool blocks back. Drop
    them and the resume fails, which is why unknown types are kept whole."""
    blocks = [
        {"type": "server_tool_use", "id": "st_1", "name": "web_search",
         "input": {"query": "st thomas flights"}},
        {"type": "web_search_tool_result", "tool_use_id": "st_1",
         "content": [{"title": "Fares", "url": "https://example.com"}]},
    ]

    assert _for_this_turn(blocks) == blocks


def test_nulls_the_sdk_invents_are_dropped_from_unknown_blocks():
    out = _for_this_turn([{"type": "redacted_thinking", "data": "abc",
                           "cache_control": None}])

    assert out == [{"type": "redacted_thinking", "data": "abc"}]


def test_a_block_that_is_not_a_dict_is_left_alone():
    assert _for_this_turn(["already text"]) == ["already text"]


# --- through the loop, which is where it actually failed ---------------------

@pytest.mark.asyncio
async def test_the_next_request_of_a_working_turn_is_clean(db, conversation, script):
    """Her St Thomas ask: a tool round, then the reply. The second request
    replays the first response — and must not carry the field back."""
    sent = script(
        _calls("send_report_email",
               {"subject": "St Thomas", "body_markdown": "## Flights"},
               text="Building it now", parsed_output={"stage": "flights"}),
        _says("Sent - the full plan is in your inbox."),
    )

    reply = await claude_service.chat(
        db, conversation.id,
        "Plan a trip to st Thomas for me and my kids making arrangements for "
        "flights for everyone and taxi when we get there and packing list for "
        "everyone and their kids")

    assert reply == "Sent - the full plan is in your inbox."
    assert len(sent) == 2
    assert _fields(sent[1]["messages"], "text") == {"type", "text"}


@pytest.mark.asyncio
async def test_it_does_not_come_back_from_the_database_either(db, conversation, script):
    """The dump is persisted too, and replay has its own sanitiser. This is the
    check that the two agree — a field the loop stripped must not reappear a day
    later out of the database."""
    sent = script(_says("On it.", parsed_output={"stage": "done"}))
    await claude_service.chat(db, conversation.id, "Plan St Thomas")

    script(_says("Here you go."))
    await claude_service.chat(db, conversation.id, "Any update?")

    # The second turn replays the first one out of the database.
    replayed = sent[-1]["messages"]
    assert any(m["role"] == "assistant" for m in replayed)
    assert _fields(replayed, "text") == {"type", "text"}
