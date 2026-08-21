"""Inbound email webhook (for the Resend provider). For the Gmail provider,
the IMAP poller calls the same processing logic instead.
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import email_inbound

logger = logging.getLogger(__name__)
router = APIRouter()


def _first(payload: dict, *keys) -> str:
    for k in keys:
        if payload.get(k):
            return str(payload[k])
    return ""


async def _parse_inbound(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        data = await request.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = {**data, **data["data"]}
        return data if isinstance(data, dict) else {}
    return dict(await request.form())


@router.post("/webhook/email")
async def receive_email(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    # Fail closed: an unset secret used to mean "no authentication at all", so
    # anyone could POST a forged sender and drive a model turn.
    supplied = request.query_params.get("secret") or request.headers.get("X-Webhook-Secret") or ""
    if not settings.email_inbound_secret or not hmac.compare_digest(
        supplied.encode("utf-8"), settings.email_inbound_secret.encode("utf-8")
    ):
        logger.warning("Inbound email rejected — missing or bad secret")
        return Response(status_code=403)

    payload = await _parse_inbound(request)

    # Resend posts every event type to the same endpoint — email.sent,
    # email.delivered, email.bounced, email.received. Only the last one is a
    # message TO Cord; treating a delivery receipt as inbound mail would feed
    # our own outgoing subject line back into a model turn.
    event_type = str(payload.get("type") or "")
    if event_type and event_type != "email.received":
        return Response(status_code=200)

    sender = email_inbound.extract_email(payload.get("from") or payload.get("sender") or payload.get("From"))
    subject = _first(payload, "subject", "Subject")
    body = _first(payload, "text", "plain", "body-plain", "stripped-text", "TextBody", "body")

    try:
        await email_inbound.process_inbound_email(db, sender, subject, body)
    except Exception as e:
        logger.error(f"Error processing inbound email: {e}", exc_info=True)

    return Response(status_code=200)
