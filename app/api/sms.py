import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, twilio_service

logger = logging.getLogger(__name__)
router = APIRouter()

TWIML_EMPTY = '<?xml version="1.0"?><Response></Response>'

_OPT_IN_CONFIRM = (
    "Cordia AI by AI-Gen Partners: You're subscribed to your personal assistant. "
    "Message frequency varies. Msg & data rates may apply. "
    "Reply HELP for help, STOP to unsubscribe."
)
_HELP_MSG = (
    "Cordia AI by AI-Gen Partners — your personal assistant. "
    "Msg & data rates may apply. Support: tyler@ai-genpartners.com. "
    "Reply STOP to unsubscribe."
)
_STOP_CONFIRM = (
    "You have successfully been unsubscribed from Cordia AI. "
    "You will not receive any more messages. Reply START to resubscribe."
)


async def _record_consent(db: AsyncSession, phone: str) -> None:
    """Insert a consent record the first time a number contacts the service."""
    await db.execute(
        text(
            "INSERT INTO sms_consent (phone, consented_at, method) "
            "VALUES (:phone, :ts, 'inbound_text') "
            "ON CONFLICT (phone) DO NOTHING"
        ),
        {"phone": phone, "ts": datetime.now(timezone.utc)},
    )
    await db.commit()


async def _record_opt_out(db: AsyncSession, phone: str) -> None:
    await db.execute(
        text("UPDATE sms_consent SET opted_out_at = :ts WHERE phone = :phone"),
        {"phone": phone, "ts": datetime.now(timezone.utc)},
    )
    await db.commit()


async def _record_opt_in(db: AsyncSession, phone: str) -> None:
    await db.execute(
        text(
            "INSERT INTO sms_consent (phone, consented_at, method) "
            "VALUES (:phone, :ts, 'keyword_start') "
            "ON CONFLICT (phone) DO UPDATE SET consented_at = EXCLUDED.consented_at, opted_out_at = NULL"
        ),
        {"phone": phone, "ts": datetime.now(timezone.utc)},
    )
    await db.commit()


@router.post("/webhook/sms")
async def receive_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not settings.debug:
        form_data = dict(await request.form())
        await twilio_service.verify_twilio_request(request, form_data)

    keyword = Body.strip().upper()

    # Handle STOP — Twilio also handles this natively, but we record it
    if keyword in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"):
        await _record_opt_out(db, From)
        await twilio_service.send_sms(to=From, body=_STOP_CONFIRM)
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    # Handle START / UNSTOP — re-subscription
    if keyword in ("START", "UNSTOP", "YES"):
        await _record_opt_in(db, From)
        await twilio_service.send_sms(to=From, body=_OPT_IN_CONFIRM)
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    # Handle HELP / INFO
    if keyword in ("HELP", "INFO"):
        await twilio_service.send_sms(to=From, body=_HELP_MSG)
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    # Whitelist — only accept from Cordia's number (or test number during testcordia phase)
    allowed = {n for n in (settings.cordia_phone_number, settings.cordia_test_phone_number) if n}
    if allowed and From not in allowed:
        logger.warning(f"SMS from unknown number {From} — rejected")
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    # Record consent on first contact
    await _record_consent(db, From)

    logger.info(f"Inbound SMS from {From}: {Body[:50]}...")

    try:
        conversation = await claude_service.get_or_create_conversation(db, From)
        response_text = await claude_service.chat(db, conversation.id, Body)
        await twilio_service.send_sms(to=From, body=response_text)
    except Exception as e:
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        await twilio_service.send_sms(
            to=From,
            body="Something went wrong on my end. Please try again in a moment.",
        )

    return Response(content=TWIML_EMPTY, media_type="application/xml")
