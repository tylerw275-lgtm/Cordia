"""The output ran out of room, and nobody was told.

Cordia asked for a St Thomas trip — flights for three families on two date
ranges, a taxi, packing lists for adults and kids — and received, verbatim:

    "I'm on it - give me a moment and ask me again if you don't hear back."

That is _FALLBACK_REPLY, returned only when the model produced no text at all.
The deliverable did not fit in 8,192 output tokens, so generation was cut off
mid-tool-call: the email tool never ran, `stop_reason` came back `max_tokens`,
and the loop dropped that into a branch marked "unexpected stop reason" with no
log, no ledger row and no alert. The message it returned invited her to ask
again, straight back into the same wall.

Three stop reasons landed in that branch. All three are handled now.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.usage import UsageEvent
from app.prompts.prompt_profiles import get_profile
from app.services import claude_service

OWNER = "+16157080002"


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _stopped(reason, text=""):
    content = [_blk(type="text", text=text)] if text else []
    return SimpleNamespace(stop_reason=reason, usage=None, container=None, content=content)


def _says(text):
    return SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                           content=[_blk(type="text", text=text)])


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
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))


# --- the model actually has room now ----------------------------------------

def test_the_configured_model_is_not_running_on_legacy_defaults():
    """The profile registry falls back to 2,048 / 8,192 for anything it does not
    recognise. Landing there silently is how the deliverable got starved."""
    profile = get_profile(settings.claude_model)
    assert profile.family != "legacy", f"no profile for {settings.claude_model}"
    assert profile.deep_max_tokens >= 16_000


def test_deep_work_gets_far_more_room_than_a_text_reply():
    profile = get_profile(settings.claude_model)
    assert profile.deep_max_tokens > profile.max_tokens * 4


def test_the_profile_sends_nothing_opus_five_rejects():
    """Thinking is on by default there; `budget_tokens` and `temperature` are
    rejected outright."""
    profile = get_profile("claude-opus-5")
    for extras in (profile.normal_extras, profile.deep_extras):
        assert "thinking" not in extras
        assert "budget_tokens" not in extras
        assert "temperature" not in extras
    assert profile.deep_extras["output_config"]["effort"] == "high"
    # Low on ordinary texts is a latency decision: she is standing there.
    assert profile.normal_extras["output_config"]["effort"] == "low"


def test_a_big_output_budget_streams():
    """Non-streaming, a slow generation hits the HTTP timeout and the turn is
    lost — the same failure by another route."""
    assert claude_service._STREAM_ABOVE_MAX_TOKENS < get_profile(
        settings.claude_model).deep_max_tokens


# --- max_tokens is no longer silent -----------------------------------------

@pytest.mark.asyncio
async def test_running_out_of_room_never_returns_the_ask_again_message(
        db, conversation, script):
    """The exact message she got, and the reason she got it twice."""
    script(_stopped("max_tokens", "## Flights\n\nDelta 1422"),
           _says("Sent the flights section; packing lists next."))

    reply = await claude_service.chat(db, conversation.id, "plan the trip")

    assert reply != claude_service._FALLBACK_REPLY
    assert "ask me again if you don't hear back" not in reply


@pytest.mark.asyncio
async def test_it_is_told_to_deliver_in_pieces(db, conversation, script):
    sent = script(_stopped("max_tokens", "## Flights"), _says("Done."))

    await claude_service.chat(db, conversation.id, "plan the trip")

    nudge = sent[1]["messages"][-1]["content"]
    assert "cut off" in nudge
    assert "save_project_findings" in nudge
    assert "did not run and nothing was sent" in nudge


@pytest.mark.asyncio
async def test_truncating_twice_hands_over_what_there_is(db, conversation, script):
    """Twice is a pattern, not a hiccup. Silence would be the old bug again."""
    partial = "## Flights\n\nDelta 1422 at $412 for the Franklin families."
    script(_stopped("max_tokens", "first"), _stopped("max_tokens", partial))

    reply = await claude_service.chat(db, conversation.id, "plan the trip")

    assert partial in reply
    assert "keep going" in reply


@pytest.mark.asyncio
async def test_truncating_with_nothing_to_show_still_says_something_true(
        db, conversation, script):
    script(_stopped("max_tokens"), _stopped("max_tokens"))

    reply = await claude_service.chat(db, conversation.id, "plan the trip")

    assert "longer than I can send in one piece" in reply
    assert "keep going" in reply


@pytest.mark.asyncio
async def test_it_only_retries_once(db, conversation, script):
    sent = script(_stopped("max_tokens", "a"), _stopped("max_tokens", "b"))
    await claude_service.chat(db, conversation.id, "plan the trip")
    assert len(sent) == 2


# --- refusal, and anything else ---------------------------------------------

@pytest.mark.asyncio
async def test_a_refusal_is_said_plainly(db, conversation, script):
    """"Give me a moment" for something that is never coming is the failure
    this whole section exists to stop."""
    script(_stopped("refusal"))

    reply = await claude_service.chat(db, conversation.id, "do something off limits")

    assert reply != claude_service._FALLBACK_REPLY
    assert "can't help with that one" in reply


@pytest.mark.asyncio
async def test_an_unrecognised_ending_still_returns_its_text(db, conversation, script):
    script(_stopped("stop_sequence", "Here is what I found."))
    reply = await claude_service.chat(db, conversation.id, "hello")
    assert reply == "Here is what I found."


# --- and every one of them is now visible -----------------------------------

@pytest.mark.parametrize("reason", ["max_tokens", "refusal", "stop_sequence"])
@pytest.mark.asyncio
async def test_an_unusual_ending_reaches_the_ledger_and_the_operator(
        db, conversation, script, mocker, reason):
    """All three used to end the turn with no log, no row and no alert. That is
    why finding this took a transcript."""
    recorded = mocker.patch("app.services.usage_service.record_standalone",
                            new=mocker.AsyncMock())
    if reason == "max_tokens":
        script(_stopped(reason, "a"), _stopped(reason, "b"))
    else:
        script(_stopped(reason, "x"))

    await claude_service.chat(db, conversation.id, "plan the trip")

    wheres = [c.kwargs.get("details", {}).get("where", "")
              for c in recorded.await_args_list]
    assert any(w == f"stop_reason:{reason}" for w in wheres), wheres


@pytest.mark.asyncio
async def test_a_normal_turn_reports_nothing_unusual(db, conversation, script, mocker):
    recorded = mocker.patch("app.services.usage_service.record_standalone",
                            new=mocker.AsyncMock())
    script(_says("Delta 1422 at $412."))

    await claude_service.chat(db, conversation.id, "which flights")

    wheres = [c.kwargs.get("details", {}).get("where", "")
              for c in recorded.await_args_list]
    assert not any(w.startswith("stop_reason:") for w in wheres)


# --- the prompt tells it before the net has to catch it ---------------------

def test_the_prompt_says_to_build_big_things_in_pieces():
    from app.prompts.system_prompt import build_system_prompt

    text = " ".join(b["text"] for b in build_system_prompt(None))
    assert "SOMETHING BIG GOES OUT IN PIECES" in text
    assert "save_project_findings" in text
    assert "three families vanished exactly this way" in text
