"""The one public endpoint that spends money.

/webhook/duffel has to be reachable by Duffel, so it is reachable by anyone.
What arrives on it records a flight booking and texts Cordia. The signature is
the only thing between a stranger and a message from her assistant telling her
her flight has changed — and until now nothing tested that it was checked.
"""
import hashlib
import hmac
import json
import time

import pytest

from app.api import duffel_webhooks
from app.config import settings

SECRET = "whsec_test_key"


def _sign(payload: bytes, secret: str = SECRET, timestamp: float | None = None) -> str:
    ts = int(time.time() if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _event(kind="order.created", order_id="ord_123") -> bytes:
    return json.dumps({"type": kind, "data": {"object": {"id": order_id}}}).encode()


@pytest.fixture(autouse=True)
def _configured(mocker):
    mocker.patch.object(settings, "duffel_webhook_secret", SECRET)
    mocker.patch.object(settings, "debug", False)
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock(return_value=True))


async def _post(client, payload: bytes, signature: str):
    return await client.post(
        "/webhook/duffel", content=payload,
        headers={"X-Duffel-Signature": signature, "content-type": "application/json"},
    )


# --- the signature ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_correctly_signed_event_is_accepted(client, mocker):
    mocker.patch("app.services.duffel_service.get_order",
                 new=mocker.AsyncMock(return_value=None))
    payload = _event()
    assert (await _post(client, payload, _sign(payload))).status_code == 200


@pytest.mark.asyncio
async def test_an_unsigned_event_is_refused(client):
    payload = _event()
    assert (await _post(client, payload, "")).status_code == 401


@pytest.mark.asyncio
async def test_a_wrongly_signed_event_is_refused(client):
    payload = _event()
    assert (await _post(client, payload, _sign(payload, secret="not-the-secret"))).status_code == 401


@pytest.mark.asyncio
async def test_a_signature_for_different_content_is_refused(client):
    """The body must be what was signed, not merely something that was."""
    signature = _sign(_event(order_id="ord_cheap"))
    assert (await _post(client, _event(order_id="ord_expensive"), signature)).status_code == 401


@pytest.mark.asyncio
async def test_a_malformed_signature_header_is_refused(client):
    payload = _event()
    for header in ("garbage", "t=123", "v1=abc", "t=,v1=", "t=notanumber,v1=abc"):
        assert (await _post(client, payload, header)).status_code == 401, header


# --- replay -----------------------------------------------------------------

def test_a_captured_delivery_cannot_be_replayed_forever():
    """order.created dedupes on the order id, but a replayed schedule-change
    event has nothing to deduplicate against and would text her every time."""
    payload = _event("order.changed")
    stale = _sign(payload, timestamp=time.time() - 3600)
    assert duffel_webhooks._verify_signature(payload, stale) is False


def test_a_signature_from_the_future_is_refused_too():
    """A clock skewed forward is not a licence to accept anything."""
    payload = _event()
    assert duffel_webhooks._verify_signature(
        payload, _sign(payload, timestamp=time.time() + 3600)) is False


def test_a_little_clock_skew_is_tolerated():
    payload = _event()
    assert duffel_webhooks._verify_signature(payload, _sign(payload, timestamp=time.time() - 30))


# --- a missing secret must not mean "accept everything" ---------------------

def test_a_missing_secret_fails_closed_in_production(mocker):
    """An unset environment variable should not silently turn into an open
    door on the endpoint that books flights."""
    mocker.patch.object(settings, "duffel_webhook_secret", "")
    mocker.patch.object(settings, "debug", False)
    assert duffel_webhooks._verify_signature(_event(), "anything") is False


def test_a_missing_secret_is_only_tolerated_in_debug(mocker):
    mocker.patch.object(settings, "duffel_webhook_secret", "")
    mocker.patch.object(settings, "debug", True)
    assert duffel_webhooks._verify_signature(_event(), "") is True


# --- what a genuine event actually does ------------------------------------

@pytest.mark.asyncio
async def test_a_booking_is_recorded_and_she_is_told(client, db, mocker):
    from app.models.trips import FlightBooking
    from sqlalchemy import select

    mocker.patch("app.services.duffel_service.get_order", new=mocker.AsyncMock(return_value={
        "order_id": "ord_123", "booking_reference": "ABC123", "origin": "BNA",
        "destination": "LGA", "total_amount": "412.30", "currency": "USD",
        "depart_time": "2026-09-01T08:00:00", "passengers": ["Cordia Harrington"],
    }))
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    payload = _event()
    assert (await _post(client, payload, _sign(payload))).status_code == 200

    booked = (await db.execute(select(FlightBooking))).scalars().all()
    assert [b.duffel_order_id for b in booked] == ["ord_123"]
    assert "ABC123" in send.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_the_same_order_is_not_recorded_twice(client, db, mocker):
    """Duffel retries. A second confirmation text for one booking reads as a
    second booking."""
    from app.models.trips import FlightBooking
    from sqlalchemy import select

    mocker.patch("app.services.duffel_service.get_order", new=mocker.AsyncMock(return_value={
        "order_id": "ord_123", "booking_reference": "ABC123", "total_amount": "412.30",
    }))
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    payload = _event()
    for _ in range(3):
        await _post(client, payload, _sign(payload))

    assert len((await db.execute(select(FlightBooking))).scalars().all()) == 1
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_is_a_400_not_a_500(client):
    payload = b"not json at all"
    assert (await _post(client, payload, _sign(payload))).status_code == 400


@pytest.mark.asyncio
async def test_an_unrecognised_event_type_is_ignored_quietly(client, mocker):
    order = mocker.patch("app.services.duffel_service.get_order", new=mocker.AsyncMock())
    payload = _event("order.airline_initiated_change_detected_but_not_really")
    assert (await _post(client, payload, _sign(payload))).status_code == 200
    assert not order.called


@pytest.mark.asyncio
async def test_nothing_is_texted_to_anyone_but_cordia(client, mocker):
    """A webhook is an unauthenticated stranger's input. It must never be able
    to choose who gets a message."""
    mocker.patch("app.services.duffel_service.get_order", new=mocker.AsyncMock(return_value={
        "order_id": "ord_123", "booking_reference": "ABC123", "total_amount": "1",
    }))
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    body = json.dumps({"type": "order.created",
                       "data": {"object": {"id": "ord_123"}, "notify": "+15559998888"}}).encode()
    await _post(client, body, _sign(body))

    assert send.await_args.kwargs["to"] == "+16157080002"


@pytest.mark.asyncio
async def test_an_unexpected_passenger_shape_does_not_lose_the_booking(client, db, mocker):
    """The column is a text array. If Duffel's shape ever changes, the insert
    fails after db.add, the webhook 500s, Duffel retries into the same failure,
    and her flight is booked with nothing to show for it."""
    from app.models.trips import FlightBooking
    from sqlalchemy import select

    mocker.patch("app.services.duffel_service.get_order", new=mocker.AsyncMock(return_value={
        "order_id": "ord_shape", "booking_reference": "ZZZ999", "total_amount": "9200.00",
        "passengers": [{"given_name": "Cordia", "family_name": "Harrington"},
                       {"name": "Tom Harrington"}, 12345],
    }))
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())

    payload = _event(order_id="ord_shape")
    assert (await _post(client, payload, _sign(payload))).status_code == 200

    booked = (await db.execute(select(FlightBooking))).scalars().all()
    assert len(booked) == 1
    assert booked[0].passengers == ["Cordia Harrington", "Tom Harrington", "12345"]
    assert send.called, "the booking saved but she was never told"
