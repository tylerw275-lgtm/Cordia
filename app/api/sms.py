import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, family_circle_service, twilio_service
from app.utils.phone import phones_match

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
    "Msg & data rates may apply. Support: tyler@aigenpartners.com. "
    "Reply STOP to unsubscribe."
)
_STOP_CONFIRM = (
    "You have successfully been unsubscribed from Cordia AI. "
    "You will not receive any more messages. Reply START to resubscribe."
)
_FAMILY_WELCOME = (
    "Hi {name}! I'm Cordia's family assistant. You can share gift ideas, tips on how "
    "you like her to connect with you, your kids' current interests, or calendar dates — "
    "and I'll pass them along to help her. Anything you share goes to Cordia. "
    "Reply STOP to opt out anytime."
)
_FAMILY_WELCOME_NEEDS_CONSENT = (
    "Hi {name}! I'm Cordia's family assistant. First, please sign the quick consent form "
    "at {consent_url} — it takes under a minute. Then you can share gift ideas, tips, "
    "your kids' current interests, or calendar dates, and I'll pass them along to help "
    "Cordia. Anything you share goes to her. Reply STOP to opt out anytime."
)


async def _has_signed_consent_form(db: AsyncSession, phone: str) -> bool:
    """True if this number submitted the electronic consent form at /consent."""
    result = await db.execute(
        text("SELECT 1 FROM sms_consent WHERE phone = :phone AND method = 'web_form'"),
        {"phone": phone},
    )
    return result.first() is not None


async def _resolve_sender(db: AsyncSession, phone: str):
    """Return (role, member). role is 'owner', 'family', or 'unknown'."""
    if phones_match(phone, settings.cordia_phone_number) or phones_match(phone, settings.cordia_test_phone_number):
        return "owner", None
    member = await family_circle_service.resolve_member_by_phone(db, phone)
    if member and member.has_circle_access:
        return "family", member
    return "unknown", None


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
    form_data = dict(await request.form())
    if not settings.debug:
        await twilio_service.verify_twilio_request(request, form_data)

    # Collect any MMS media (images). Downloaded only after the sender is authorized.
    try:
        num_media = int(form_data.get("NumMedia", "0") or 0)
    except ValueError:
        num_media = 0
    media = [
        (form_data.get(f"MediaUrl{i}"), form_data.get(f"MediaContentType{i}"))
        for i in range(num_media)
        if form_data.get(f"MediaUrl{i}")
    ]

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

    # Resolve who this is: Cordia (owner), an opted-in family member, or unknown
    role, member = await _resolve_sender(db, From)
    if role == "unknown":
        logger.warning(f"SMS from unknown number {From} — rejected")
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    # Record consent on first contact
    await _record_consent(db, From)

    # First time a family member texts in: send the transparent welcome.
    # If they haven't signed the electronic consent form yet, point them to it.
    if role == "family" and member.circle_consented_at is None:
        await family_circle_service.record_consent(db, member)
        first_name = member.nickname or member.name.split()[0]
        if await _has_signed_consent_form(db, From):
            welcome = _FAMILY_WELCOME.format(name=first_name)
        else:
            welcome = _FAMILY_WELCOME_NEEDS_CONSENT.format(
                name=first_name, consent_url=f"{settings.public_base_url}/consent"
            )
        await twilio_service.send_sms(to=From, body=welcome)

    logger.info(f"Inbound SMS from {From} (role={role}, media={len(media)}): {Body[:50]}...")

    # Download images only now that the sender is authorized
    images = await twilio_service.fetch_image_blocks(media) if media else []

    try:
        conversation = await claude_service.get_or_create_conversation(db, From)
        response_text = await claude_service.chat(
            db, conversation.id, Body, sender_role=role, sender_member=member, images=images
        )
        await twilio_service.send_sms(to=From, body=response_text)
    except Exception as e:
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        await twilio_service.send_sms(
            to=From,
            body="Something went wrong on my end. Please try again in a moment.",
        )

    return Response(content=TWIML_EMPTY, media_type="application/xml")
