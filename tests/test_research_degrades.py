"""Research must never be able to break texting.

Cordia texted and got "Something went wrong on my end." The web tools had gone
live by default, and a tool definition the API would not accept took down the
whole conversation — the newest feature breaking the oldest one. A capability
that fails should quietly drop, not kill the assistant.
"""
import uuid
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.usage import UsageEvent
from app.services import claude_service, usage_service


def _block(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _response(text="done", stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[_block(type="text", text=text)], usage=None
    )


def _bad_request(message="tools.1: unexpected tool type"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return anthropic.BadRequestError(message, response=response, body=None)


def _overloaded():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(529, request=request, json={"error": {"message": "overloaded"}})
    return anthropic.InternalServerError("overloaded", response=response, body=None)


@pytest.fixture
def loop(db, mocker):
    mocker.patch.object(claude_service, "_persist_message", new=mocker.AsyncMock())
    mocker.patch.object(claude_service, "_load_history", new=mocker.AsyncMock(return_value=[]))
    mocker.patch.object(
        claude_service, "_build_owner_system",
        new=mocker.AsyncMock(return_value=[{"type": "text", "text": "sys"}]),
    )
    mocker.patch.object(claude_service, "get_tool_schemas", return_value=[])
    mocker.patch.object(settings, "enable_web_research", True)
    return mocker


@pytest.mark.asyncio
async def test_a_rejected_web_tool_retries_without_it_and_still_answers(db, loop, mocker):
    """The actual production failure: Cordia got an error instead of a reply."""
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[_bad_request(), _response("Here you go.")]),
    )

    out = await claude_service.chat(db, uuid.uuid4(), "what's the weather in naples")

    assert out == "Here you go."
    assert create.await_count == 2
    first = {t["name"] for t in create.await_args_list[0].kwargs["tools"]}
    second = {t["name"] for t in create.await_args_list[1].kwargs["tools"]}
    assert "web_search" in first
    assert "web_search" not in second, "the retry still carried the rejected tool"


@pytest.mark.asyncio
async def test_the_retry_happens_at_most_once(db, loop, mocker):
    """If the request is bad for some other reason, retrying forever just adds
    latency before failing anyway."""
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=_bad_request()),
    )

    with pytest.raises(anthropic.BadRequestError):
        await claude_service.chat(db, uuid.uuid4(), "hello")

    assert create.await_count == 2


@pytest.mark.asyncio
async def test_a_real_outage_is_not_retried_without_tools(db, loop, mocker):
    """A 529 is not a request-shape problem. Dropping research would not help
    and would hide a genuine outage."""
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=_overloaded()),
    )

    with pytest.raises(anthropic.InternalServerError):
        await claude_service.chat(db, uuid.uuid4(), "hello")

    assert create.await_count == 1


@pytest.mark.asyncio
async def test_a_deep_ask_says_research_was_unavailable(db, loop, mocker):
    """Otherwise she assumes a price or a date was looked up when it came from
    memory — which is how a stale number gets acted on."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[_bad_request(), _response("Roughly $700.")]),
    )

    out = await claude_service.chat(db, uuid.uuid4(), "find me a car service and price it")

    assert "Roughly $700." in out
    assert "couldn't search the web" in out


@pytest.mark.asyncio
async def test_a_casual_message_is_not_cluttered_with_the_caveat(db, loop, mocker):
    """If research is down for days, every reply carrying a disclaimer is noise."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[_bad_request(), _response("Morning!")]),
    )

    out = await claude_service.chat(db, uuid.uuid4(), "good morning")

    assert out == "Morning!"


@pytest.mark.asyncio
async def test_no_web_tools_means_no_retry(db, loop, mocker):
    """With research off there is nothing to drop, so the error must surface."""
    mocker.patch.object(settings, "enable_web_research", False)
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=_bad_request()),
    )

    with pytest.raises(anthropic.BadRequestError):
        await claude_service.chat(db, uuid.uuid4(), "hello")

    assert create.await_count == 1


# --- failures become visible ------------------------------------------------

@pytest.mark.asyncio
async def test_an_error_is_recorded_without_the_message_body(db):
    """A traceback can quote the user's own text. The ledger stores what broke
    and where, never what was said."""
    await usage_service.record_error(
        "sms_reply", ValueError("failed on: 'my bank password is hunter2'"),
        actor="+16155551234",
    )

    row = (await db.execute(select(UsageEvent))).scalars().one()
    assert row.event_type == "error"
    assert row.details == {"where": "sms_reply", "error_type": "ValueError"}
    assert "hunter2" not in str(row.details)
    assert float(row.cost_usd) == 0


@pytest.mark.asyncio
async def test_errors_do_not_count_as_spend(db):
    await usage_service.record(db, "sms_out", actor="+1615", cost_usd=0.50)
    await usage_service.record_error("sms_reply", RuntimeError("boom"))

    s = await usage_service.summary(db)
    assert s["usage_cost"] == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_recent_failures_reach_the_dashboard(db, client, mocker):
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    await usage_service.record_error("sms_reply", _bad_request(), actor="+16158539483")

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "Recent failures" in html
    assert "BadRequestError" in html
    assert "(615) 853-9483" in html
