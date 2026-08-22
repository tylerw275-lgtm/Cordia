"""A turn ends when the model stops replying. Nothing resumes it.

Cordia asked for a St Thomas trip — flights for three families, a taxi, packing
lists. She answered a full interview. Then:

    "Yes, still on it! Almost done - sending to your inbox in just a minute."
    "Didn't get it"
    "Sorry about that - let me finish building it and get it to you right now."

And nothing. No email, no message, ever. Not a crash: the turn ended cleanly on
a promise, and there is no later in which to keep it. She was left waiting on
work that had already stopped.

The prompt now says so outright, and this is the net underneath it.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.authorized_user import AuthorizedUser
from app.services import claude_service

OWNER = "+16157080002"


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _says(text):
    return SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                           content=[_blk(type="text", text=text)])


def _calls(tool, tool_input=None, *, tool_id="tu_1", text=""):
    content = ([_blk(type="text", text=text)] if text else []) + [
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


# --- the exact failure -------------------------------------------------------

@pytest.mark.asyncio
async def test_the_promise_that_ended_her_conversation_is_caught(db, conversation, script):
    """Her real last message, verbatim."""
    sent = script(
        _says("Sorry about that - let me finish building it and get it to you right now."),
        _calls("send_report_email",
               {"subject": "St Thomas", "body_markdown": "## Flights"}),
        _says("Sent - the full plan is in your inbox."),
    )

    reply = await claude_service.chat(db, conversation.id, "Didn't get it")

    assert reply == "Sent - the full plan is in your inbox."
    assert len(sent) == 3, "the promise was accepted and the turn ended"


@pytest.mark.asyncio
async def test_the_model_is_told_there_is_no_later(db, conversation, script):
    sent = script(
        _says("Almost done - sending to your inbox in just a minute."),
        _says("I have not built it yet. Tell me to keep going and I will."),
    )

    await claude_service.chat(db, conversation.id, "still working?")

    nudge = sent[1]["messages"][-1]["content"]
    assert "There is no later" in nudge
    assert "nothing runs in the background" in nudge


@pytest.mark.asyncio
async def test_admitting_it_is_not_finished_is_allowed(db, conversation, script):
    """The nudge must not force a fabricated delivery — saying so plainly is a
    correct outcome."""
    honest = "I have not finished it. Say keep going and I will pick it back up."
    script(_says("I'll send that shortly."), _says(honest))

    assert await claude_service.chat(db, conversation.id, "where is it") == honest


# --- it must not fire on a real delivery -------------------------------------

@pytest.mark.asyncio
async def test_a_reply_that_actually_sent_is_left_alone(db, conversation, script):
    """The tool ran, so there is nothing to chase."""
    sent = script(
        _calls("send_report_email", {"subject": "St Thomas", "body_markdown": "## Plan"}),
        _says("Sent - it is in your inbox now, with flights and the taxi."),
    )

    reply = await claude_service.chat(db, conversation.id, "send me the plan")

    assert reply.startswith("Sent")
    assert len(sent) == 2, "a genuine delivery was second-guessed"


@pytest.mark.parametrize("reply", [
    "Here are the three options: Delta 1422 at $412, JetBlue 1155 at $389.",
    "Kelly Taxis quoted $40 each way for up to four people.",
    "Let me know which you prefer.",
    "Southwest is the only carrier I cannot check.",
])
@pytest.mark.asyncio
async def test_an_ordinary_answer_is_not_treated_as_a_promise(
        db, conversation, script, reply):
    sent = script(_says(reply))
    assert await claude_service.chat(db, conversation.id, "which flights") == reply
    assert len(sent) == 1, f"needlessly sent back: {reply!r}"


# --- it cannot loop ----------------------------------------------------------

@pytest.mark.asyncio
async def test_it_gives_up_after_one_attempt(db, conversation, script):
    """A model that keeps promising must not keep being asked. One nudge, then
    her reply goes out as-is — she is better served by an odd answer than by
    silence."""
    sent = script(
        _says("I'll send it shortly."),
        _says("Sending it over in a minute."),
    )

    reply = await claude_service.chat(db, conversation.id, "where is it")

    assert reply == "Sending it over in a minute."
    assert len(sent) == 2, "nudged more than once"


@pytest.mark.asyncio
async def test_delivering_after_the_nudge_clears_it(db, conversation, script):
    sent = script(
        _says("Give me a moment while I pull it together."),
        _calls("deliver_project", {"project_id": "x", "deliverable": "## Plan"}),
        _says("Done - sent."),
    )

    await claude_service.chat(db, conversation.id, "the trip plan")
    assert len(sent) == 3


# --- what counts as delivering ----------------------------------------------

def test_the_delivery_tools_are_the_ones_that_reach_her():
    tools = claude_service._DELIVERY_TOOLS
    assert "send_report_email" in tools
    assert "deliver_project" in tools
    # Reading is not delivering: a turn that only looked something up and then
    # promised to send it is the exact failure.
    assert "list_projects" not in tools
    assert "recall_memory" not in tools


def test_the_prompt_says_it_before_the_net_has_to_catch_it():
    from app.prompts.system_prompt import build_system_prompt

    text = " ".join(b["text"] for b in build_system_prompt(None))
    assert "THERE IS NO LATER" in text
    assert "sending to your inbox in a" in text
    assert "heard nothing ever again" in text
