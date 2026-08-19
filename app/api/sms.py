import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, family_circle_service, sms_service, twilio_service
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


async def _process_inbound(db: AsyncSession, from_number: str, body: str, media: list) -> None:
    """Provider-agnostic inbound SMS handling: keywords, sender resolution,
    consent recording, family welcome, and the AI conversation. Both the
    Twilio and Signal House webhooks land here; replies go out via the
    active provider (sms_service)."""
    keyword = (body or "").strip().upper()

    # Handle STOP — providers also handle this natively, but we record it
    if keyword in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"):
        await _record_opt_out(db, from_number)
        await sms_service.send_sms(to=from_number, body=_STOP_CONFIRM)
        return

    # Handle START / UNSTOP — re-subscription
    if keyword in ("START", "UNSTOP", "YES"):
        await _record_opt_in(db, from_number)
        await sms_service.send_sms(to=from_number, body=_OPT_IN_CONFIRM)
        return

    # Handle HELP / INFO
    if keyword in ("HELP", "INFO"):
        await sms_service.send_sms(to=from_number, body=_HELP_MSG)
        return

    # Resolve who this is: Cordia (owner), an opted-in family member, or unknown
    role, member = await _resolve_sender(db, from_number)
    if role == "unknown":
        logger.warning(f"SMS from unknown number {from_number} — rejected")
        return

    # Record consent on first contact
    await _record_consent(db, from_number)

    # First time a family member texts in: send the transparent welcome.
    # If they haven't signed the electronic consent form yet, point them to it.
    if role == "family" and member.circle_consented_at is None:
        await family_circle_service.record_consent(db, member)
        first_name = member.nickname or member.name.split()[0]
        if await _has_signed_consent_form(db, from_number):
            welcome = _FAMILY_WELCOME.format(name=first_name)
        else:
            welcome = _FAMILY_WELCOME_NEEDS_CONSENT.format(
                name=first_name, consent_url=f"{settings.public_base_url}/consent"
            )
        await sms_service.send_sms(to=from_number, body=welcome)

    logger.info(f"Inbound SMS from {from_number} (role={role}, media={len(media)}): {body[:50]}...")

    # Download images only now that the sender is authorized (Twilio MMS only)
    images = await twilio_service.fetch_image_blocks(media) if media else []

    try:
        conversation = await claude_service.get_or_create_conversation(db, from_number)
        response_text = await claude_service.chat(
            db, conversation.id, body, sender_role=role, sender_member=member, images=images
        )
        await sms_service.send_sms(to=from_number, body=response_text)
    except Exception as e:
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        await sms_service.send_sms(
            to=from_number,
            body="Something went wrong on my end. Please try again in a moment.",
        )


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

    await _process_inbound(db, From, Body, media)
    return Response(content=TWIML_EMPTY, media_type="application/xml")


@router.post("/webhook/signalhouse")
async def receive_signalhouse_sms(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Inbound SMS from Signal House. Configure the webhook in their dashboard
    as: https://cordia.aigenpartners.com/webhook/signalhouse?secret=<SIGNALHOUSE_WEBHOOK_SECRET>

    Payload field names vary by provider; we accept the common variants and
    finalize against their docs. Authenticated by shared secret (query param
    or X-Webhook-Secret header)."""
    supplied = request.query_params.get("secret") or request.headers.get("X-Webhook-Secret", "")
    if not settings.signalhouse_webhook_secret or supplied != settings.signalhouse_webhook_secret:
        if not settings.debug:
            logger.warning("Signal House webhook rejected: bad or missing shared secret")
            return {"status": "unauthorized"}

    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]

    from_number = next(
        (payload.get(k) for k in ("from", "From", "fromNumber", "from_number", "sender", "msisdn") if payload.get(k)),
        None,
    )
    body = next(
        (payload.get(k) for k in ("text", "body", "Body", "message", "content") if payload.get(k)),
        "",
    )
    if not from_number or not str(body).strip():
        logger.warning(f"Signal House webhook: unrecognized payload keys {list(payload)[:10]}")
        return {"status": "ignored"}

    await _process_inbound(db, str(from_number), str(body), media=[])
    return {"status": "ok"}
