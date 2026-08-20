"""STOP must actually stop the proactive traffic.

opted_out_at was recorded and then never consulted: no scheduled job and no
webhook checked it, so the STOP confirmation promised "you will not receive any
more messages" and the morning brief arrived the next day anyway.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.services import sms_service

OWNER = "+15551230000"


async def _record(db, phone, opted_out: bool):
    await db.execute(
        text(
            "INSERT INTO sms_consent (phone, consented_at, method, opted_out_at) "
            "VALUES (:p, :ts, 'inbound_text', :out) "
            "ON CONFLICT (phone) DO UPDATE SET opted_out_at = EXCLUDED.opted_out_at"
        ),
        {"p": phone, "ts": datetime.now(timezone.utc),
         "out": datetime.now(timezone.utc) if opted_out else None},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_opted_out_number_is_detected(db):
    await _record(db, OWNER, opted_out=True)
    assert await sms_service.is_opted_out(db, OWNER) is True


@pytest.mark.asyncio
async def test_subscribed_number_is_not_opted_out(db):
    await _record(db, OWNER, opted_out=False)
    assert await sms_service.is_opted_out(db, OWNER) is False


@pytest.mark.asyncio
async def test_unknown_number_is_not_opted_out(db):
    assert await sms_service.is_opted_out(db, "+15559999999") is False


@pytest.mark.asyncio
async def test_opt_out_matches_regardless_of_formatting(db):
    """Consent rows are +E164; family profiles carry bare digits."""
    await _record(db, "+15551230000", opted_out=True)
    for variant in ("5551230000", "15551230000", "(555) 123-0000"):
        assert await sms_service.is_opted_out(db, variant) is True


@pytest.mark.asyncio
async def test_send_is_suppressed_after_stop(db, mocker):
    await _record(db, OWNER, opted_out=True)
    provider = mocker.patch("app.services.twilio_service.send_sms", new=mocker.AsyncMock())
    mocker.patch("app.services.sms_service.get_db_session", create=True)
    mocker.patch("app.services.sms_service.is_opted_out", new=mocker.AsyncMock(return_value=True))

    sent = await sms_service.send_sms(to=OWNER, body="Good morning!")
    assert sent is False
    assert not provider.called


@pytest.mark.asyncio
async def test_forced_send_still_goes_out(mocker):
    """The STOP confirmation and HELP are carrier-mandated and must reach an
    opted-out number."""
    provider = mocker.patch("app.services.twilio_service.send_sms", new=mocker.AsyncMock())
    opt_check = mocker.patch("app.services.sms_service.is_opted_out", new=mocker.AsyncMock(return_value=True))

    sent = await sms_service.send_sms(to=OWNER, body="You have been unsubscribed.", force=True)
    assert sent is True
    assert provider.called
    assert not opt_check.called  # not even consulted
