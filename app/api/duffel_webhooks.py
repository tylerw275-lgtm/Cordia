"""Duffel webhook receiver + booking redirect landing pages.

Handles `order.created` (fired when Cordia completes a Duffel Links checkout):
stores the booking and texts her a confirmation. Airline-initiated change events
are surfaced to her as proactive alerts.
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.models.trips import FlightBooking
from app.services import claude_service, duffel_service, sms_service

logger = logging.getLogger(__name__)
router = APIRouter()

_LANDING = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Cordia AI</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 20px;color:#222;line-height:1.6;text-align:center}}
h1{{font-size:1.3rem}}</style></head>
<body><h1>{title}</h1><p>{message}</p></body></html>"""


def _verify_signature(payload: bytes, signature_header: str, *, now: float | None = None) -> bool:
    """Verify Duffel's webhook signature (t=<ts>,v1=<hmac-sha256>).

    This endpoint is public — it has to be, Duffel calls it — and what arrives
    on it books flights and texts Cordia. The signature is the only thing
    standing between a stranger and a message from her assistant saying her
    flight has changed.

    The timestamp is part of what is signed, and it is checked. Without that,
    one captured delivery can be replayed forever: order.created is idempotent
    on the order id, but a replayed schedule-change event has nothing to
    deduplicate against and would text her about the same change every time.
    """
    secret = settings.duffel_webhook_secret
    if not secret:
        if not settings.debug:
            # Refuse rather than fall open. An unset secret in production is a
            # misconfiguration, and treating it as "accept everything" turns a
            # missing environment variable into an open door.
            logger.error(
                "Duffel webhook rejected: DUFFEL_WEBHOOK_SECRET is not set. "
                "Set it from the Duffel dashboard webhook config."
            )
            return False
        logger.warning("Duffel webhook accepted without a secret (debug mode only)")
        return True
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp, received = parts["t"], parts["v1"]
    except (ValueError, KeyError):
        return False

    max_age = settings.duffel_webhook_max_age_seconds
    if max_age > 0:
        try:
            age = (time.time() if now is None else now) - float(timestamp)
        except ValueError:
            return False
        if abs(age) > max_age:
            logger.warning(f"Duffel webhook rejected: signature is {int(age)}s old")
            return False

    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def _passenger_names(passengers) -> list[str] | None:
    """Passenger names as a list of strings, whatever Duffel sent.

    The column is a text array. duffel_service already flattens names, but this
    is the money path: if the shape ever differs — an API version bump, a
    partner order, a change upstream — the insert fails after db.add, the
    webhook 500s, Duffel retries into the same failure, and the booking is
    never recorded. Cordia's flight is booked and her assistant never tells her.
    A name rendered awkwardly is a far better outcome than that.
    """
    if not passengers:
        return None
    names = []
    for passenger in passengers:
        if isinstance(passenger, str):
            names.append(passenger)
        elif isinstance(passenger, dict):
            full = f"{passenger.get('given_name', '')} {passenger.get('family_name', '')}".strip()
            names.append(full or passenger.get("name") or str(passenger))
        else:
            names.append(str(passenger))
    return names or None


async def _handle_order_created(db: AsyncSession, order_id: str) -> None:
    # Idempotency: skip if we already recorded this order
    existing = await db.execute(
        select(FlightBooking).where(FlightBooking.duffel_order_id == order_id)
    )
    if existing.scalar_one_or_none():
        return

    order = await duffel_service.get_order(order_id)
    if not order:
        logger.error(f"Duffel webhook: could not fetch order {order_id}")
        return

    depart = None
    if order.get("depart_time"):
        try:
            depart = datetime.fromisoformat(order["depart_time"])
        except ValueError:
            pass

    booking = FlightBooking(
        duffel_order_id=order["order_id"],
        booking_reference=order.get("booking_reference"),
        origin=order.get("origin"),
        destination=order.get("destination"),
        depart_time=depart,
        total_amount=order.get("total_amount"),
        currency=order.get("currency") or "USD",
        passengers=_passenger_names(order.get("passengers")),
        raw_order=order,
    )
    db.add(booking)
    await db.commit()

    if settings.cordia_phone_number:
        route = f"{order.get('origin') or '?'} → {order.get('destination') or '?'}"
        ref = order.get("booking_reference") or order["order_id"]
        msg = (
            f"Your flight is booked! {route}, confirmation {ref}. "
            f"Total ${order.get('total_amount')}. I've saved all the details — "
            "just ask if you need them."
        )
        await sms_service.send_sms(to=settings.cordia_phone_number, body=msg)
        await claude_service.record_assistant_message(db, settings.cordia_phone_number, msg)
    logger.info(f"Duffel booking recorded: {order_id}")


@router.post("/webhook/duffel")
async def duffel_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    payload = await request.body()
    signature = request.headers.get("X-Duffel-Signature", "")
    if not _verify_signature(payload, signature):
        logger.warning("Duffel webhook: invalid signature — rejected")
        return Response(status_code=401)

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return Response(status_code=400)

    event_type = event.get("type", "")
    data = event.get("data") or {}

    if event_type == "order.created":
        order_id = (data.get("object") or data).get("id") or data.get("order_id")
        if order_id:
            await _handle_order_created(db, order_id)
    elif event_type.startswith("order.") and "change" in event_type:
        # Airline-initiated schedule change — alert Cordia
        order_id = (data.get("object") or data).get("id")
        if settings.cordia_phone_number and order_id:
            order = await duffel_service.get_order(order_id)
            ref = (order or {}).get("booking_reference") or order_id
            msg = (
                f"Heads up — the airline changed your flight (confirmation {ref}). "
                "Want me to pull up the new details?"
            )
            await sms_service.send_sms(to=settings.cordia_phone_number, body=msg)
            await claude_service.record_assistant_message(db, settings.cordia_phone_number, msg)
    else:
        logger.info(f"Duffel webhook: ignoring event type {event_type}")

    return Response(status_code=200)


@router.get("/booking/complete", include_in_schema=False)
async def booking_complete():
    return HTMLResponse(_LANDING.format(
        title="Booking complete",
        message="You're all set! Your assistant will text you the confirmation shortly. You can close this page.",
    ))


@router.get("/booking/failed", include_in_schema=False)
async def booking_failed():
    return HTMLResponse(_LANDING.format(
        title="Booking didn't go through",
        message="Something went wrong with the booking. Text your assistant and she'll send you a fresh link.",
    ))


@router.get("/booking/abandoned", include_in_schema=False)
async def booking_abandoned():
    return HTMLResponse(_LANDING.format(
        title="Booking not completed",
        message="No problem — nothing was charged. Text your assistant whenever you'd like a new booking link.",
    ))
