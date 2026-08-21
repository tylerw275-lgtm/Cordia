"""Cost tracking.

Recording is best-effort by design — a ledger failure must never cost us the
message it was recording — which means a silently broken ledger looks exactly
like a quiet month. These tests assert rows actually land.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.usage import UsageEvent
from app.services import usage_service


async def _count(db) -> int:
    return (await db.execute(select(func.count(UsageEvent.id)))).scalar()


# --- segment counting ------------------------------------------------------

def test_short_text_is_one_segment():
    assert usage_service.sms_segments("Got it - working on this now.") == 1


def test_long_text_bills_per_segment_not_per_message():
    """A 400-character reply costs three times a short one. Counting messages
    would understate a chatty month badly."""
    assert usage_service.sms_segments("a" * 400) == 3


def test_boundary_at_160_characters():
    assert usage_service.sms_segments("a" * 160) == 1
    assert usage_service.sms_segments("a" * 161) == 2


def test_one_emoji_drops_the_limit_to_70():
    """A single non-GSM character re-encodes the whole message as UCS-2."""
    assert usage_service.sms_segments("a" * 100) == 1
    assert usage_service.sms_segments("a" * 100 + "👋") == 2


def test_empty_body_still_counts_as_one():
    assert usage_service.sms_segments("") == 1


# --- pricing ---------------------------------------------------------------

def test_model_rate_matches_by_prefix():
    assert usage_service.model_rate("claude-sonnet-4-6") == (3.00, 15.00)
    assert usage_service.model_rate("claude-opus-5") == (5.00, 25.00)


def test_unknown_model_falls_back_rather_than_billing_zero():
    """Billing an unrecognised model at zero would hide real spend."""
    assert usage_service.model_rate("some-future-model") == usage_service._DEFAULT_MODEL_RATE


def test_ai_turn_cost_from_a_usage_block():
    usage = SimpleNamespace(
        input_tokens=1_000_000, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    assert usage_service.ai_turn_cost("claude-sonnet-4-6", usage) == pytest.approx(3.00)


def test_cached_reads_are_billed_at_a_tenth():
    usage = SimpleNamespace(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0,
    )
    assert usage_service.ai_turn_cost("claude-sonnet-4-6", usage) == pytest.approx(0.30)


def test_server_tool_counts_read_from_usage():
    usage = SimpleNamespace(server_tool_use=SimpleNamespace(
        web_search_requests=4, web_fetch_requests=2))
    assert usage_service.server_tool_counts(usage) == {"web_search": 4, "web_fetch": 2}


def test_server_tool_counts_absent_is_empty_not_an_error():
    assert usage_service.server_tool_counts(SimpleNamespace()) == {}


# --- the ledger ------------------------------------------------------------

@pytest.mark.asyncio
async def test_recording_actually_writes_a_row(db):
    """The recorder swallows its own errors, so a broken ledger reads as a
    quiet month. Assert the row exists rather than that nothing raised."""
    await usage_service.record(db, "sms_out", actor="+16155551234", quantity=2, cost_usd=0.0158)

    row = (await db.execute(select(UsageEvent))).scalars().one()
    assert row.event_type == "sms_out"
    assert row.actor == "+16155551234"
    assert float(row.cost_usd) == pytest.approx(0.0158)


@pytest.mark.asyncio
async def test_sub_cent_costs_are_not_rounded_to_zero(db):
    """One SMS segment costs well under a cent. Cent precision would floor the
    entire ledger to nothing."""
    await usage_service.record(db, "email_out", actor="a@b.com", cost_usd=0.0004)
    row = (await db.execute(select(UsageEvent))).scalars().one()
    assert float(row.cost_usd) > 0


@pytest.mark.asyncio
async def test_summary_totals_by_type_and_person(db):
    await usage_service.record(db, "sms_out", actor="+1615", quantity=3, cost_usd=0.03)
    await usage_service.record(db, "sms_in", actor="+1615", quantity=1, cost_usd=0.01)
    await usage_service.record(db, "ai_turn", actor="+1901", cost_usd=0.25)

    s = await usage_service.summary(db)

    assert s["by_type"]["sms_out"] == {"quantity": 3, "cost": pytest.approx(0.03)}
    assert s["total_cost"] == pytest.approx(0.29)
    assert s["by_actor"][0]["actor"] == "+1901"  # ranked by spend
    assert {a["actor"] for a in s["by_actor"]} == {"+1615", "+1901"}


@pytest.mark.asyncio
async def test_summary_respects_the_period(db):
    await usage_service.record(db, "sms_out", actor="+1615", cost_usd=1.00)
    old = UsageEvent(
        event_type="sms_out", actor="+1615", quantity=1, cost_usd=99.0,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db.add(old)
    await db.commit()

    this_month = await usage_service.summary(
        db, since=datetime.now(timezone.utc) - timedelta(days=30)
    )
    assert this_month["total_cost"] == pytest.approx(1.00)
    assert (await usage_service.summary(db))["total_cost"] == pytest.approx(100.00)


@pytest.mark.asyncio
async def test_tokens_are_captured_for_ai_turns(db):
    usage = SimpleNamespace(
        input_tokens=1200, output_tokens=340,
        cache_read_input_tokens=900, cache_creation_input_tokens=0,
    )
    await usage_service.record(
        db, "ai_turn", actor="+1615", model="claude-sonnet-4-6", usage=usage,
        cost_usd=usage_service.ai_turn_cost("claude-sonnet-4-6", usage),
    )
    s = await usage_service.summary(db)
    assert s["input_tokens"] == 1200
    assert s["output_tokens"] == 340


# --- instrumentation is actually wired in ----------------------------------

@pytest.mark.asyncio
async def test_sending_a_text_bills_it(db, mocker):
    mocker.patch.object(settings, "sms_provider", "signalhouse")
    mocker.patch("app.services.signalhouse_service.send_sms", new=mocker.AsyncMock())
    mocker.patch("app.services.sms_service.is_opted_out", new=mocker.AsyncMock(return_value=False))
    from app.services import sms_service

    await sms_service.send_sms(to="+16155551234", body="a" * 200)

    row = (await db.execute(select(UsageEvent))).scalars().one()
    assert row.event_type == "sms_out"
    assert row.quantity == 2  # 200 chars = two segments
    assert row.actor == "+16155551234"


@pytest.mark.asyncio
async def test_a_suppressed_text_is_not_billed(db, mocker):
    """An opted-out number never receives anything, so it costs nothing."""
    mocker.patch.object(settings, "sms_provider", "signalhouse")
    send = mocker.patch("app.services.signalhouse_service.send_sms", new=mocker.AsyncMock())
    mocker.patch("app.services.sms_service.is_opted_out", new=mocker.AsyncMock(return_value=True))
    from app.services import sms_service

    assert await sms_service.send_sms(to="+16155551234", body="hi") is False
    assert not send.called
    assert await _count(db) == 0


@pytest.mark.asyncio
async def test_a_failed_email_is_not_billed(db, mocker):
    """Counting sends that never left would quietly inflate every report."""
    mocker.patch.object(settings, "enable_email", True)
    mocker.patch.object(settings, "email_provider", "resend")
    mocker.patch.object(settings, "email_api_key", "re_test")
    mocker.patch("app.services.email_service.from_address", return_value="cord@mail.example.com")
    mocker.patch(
        "app.services.email_service._send_resend",
        new=mocker.AsyncMock(return_value={"sent": False, "reason": "boom"}),
    )
    from app.services import email_service

    await email_service.send_email(to="a@b.com", subject="s", body_markdown="b")
    assert await _count(db) == 0


# --- it reaches the dashboard ----------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_renders_the_cost_card(db, client, mocker):
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    await usage_service.record(db, "sms_out", actor="+16158539483", quantity=14, cost_usd=0.1106)
    await usage_service.record(db, "web_search", actor="+16158539483", quantity=11, cost_usd=0.11)

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "What it costs" in html
    assert "Web searches" in html
    assert "(615) 853-9483" in html          # attributed to a person, readable
    assert "$0.22" in html                    # 0.1106 + 0.11, rounded
    assert "How these are worked out" in html  # the rates are always stated


@pytest.mark.asyncio
async def test_dashboard_survives_an_empty_ledger(db, client, mocker):
    """A brand-new deploy has no usage. The card must still render."""
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "What it costs" in html
    assert "fills in as Cord is used" in html


@pytest.mark.asyncio
async def test_sub_cent_totals_are_not_displayed_as_zero(db, client, mocker):
    """'$0.00' on a working ledger reads as 'tracking is broken'."""
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    await usage_service.record(db, "email_out", actor="a@b.com", cost_usd=0.0004)

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "$0.0004" in html
