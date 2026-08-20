import asyncio
import hmac
import logging
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response
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


# Providers retry a webhook whose response is slow, and an AI round-trip can
# take many seconds. We acknowledge immediately, process in the background,
# and drop repeat deliveries of the same provider message id — otherwise one
# inbound text produces several duplicate replies.
_RECENT_MESSAGE_IDS: "OrderedDict[str, None]" = OrderedDict()
_RECENT_IDS_MAX = 500


def _is_duplicate(message_id: str | None) -> bool:
    """True if this provider message id was already accepted (retry delivery)."""
    if not message_id:
        return False
    if message_id in _RECENT_MESSAGE_IDS:
        return True
    _RECENT_MESSAGE_IDS[message_id] = None
    while len(_RECENT_MESSAGE_IDS) > _RECENT_IDS_MAX:
        _RECENT_MESSAGE_IDS.popitem(last=False)
    return False


async def _process_inbound_bg(from_number: str, body: str, media: list) -> None:
    """Background entry point — opens its own DB session (the request-scoped
    one is closed once the webhook responds) and never raises into the app."""
    from app.database import get_db_session
    try:
        async with get_db_session() as db:
            await _process_inbound(db, from_number, body, media)
    except Exception as e:
        logger.error(f"Background inbound processing failed: {e}", exc_info=True)


# If a reply is still being composed after this many seconds, send a short
# courtesy note so she isn't left wondering whether it landed.
_SLOW_REPLY_SECONDS = 4.0
_WORKING_NOTE = "Got it - working on this now, one moment."


async def _notify_if_slow(to: str) -> None:
    """Sleep, then send a brief 'working on it' note. Cancelled if the real
    reply is ready first, so fast answers never get a preamble."""
    try:
        await asyncio.sleep(_SLOW_REPLY_SECONDS)
        await sms_service.send_sms(to=to, body=_WORKING_NOTE)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Could not send slow-reply note: {e}")


async def _process_inbound(db: AsyncSession, from_number: str, body: str, media: list) -> None:
    """Provider-agnostic inbound SMS handling: keywords, sender resolution,
    consent recording, family welcome, and the AI conversation. Both the
    Twilio and Signal House webhooks land here; replies go out via the
    active provider (sms_service)."""
    keyword = (body or "").strip().upper()

    # Handle STOP — providers also handle this natively, but we record it
    if keyword in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"):
        await _record_opt_out(db, from_number)
        # force: the opt-out confirmation itself must reach a now-opted-out number
        await sms_service.send_sms(to=from_number, body=_STOP_CONFIRM, force=True)
        return

    # Handle START / UNSTOP — re-subscription
    if keyword in ("START", "UNSTOP", "YES"):
        await _record_opt_in(db, from_number)
        await sms_service.send_sms(to=from_number, body=_OPT_IN_CONFIRM)
        return

    # Handle HELP / INFO
    if keyword in ("HELP", "INFO"):
        # force: HELP is carrier-mandated and must always be answered
        await sms_service.send_sms(to=from_number, body=_HELP_MSG, force=True)
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

    slow_note = asyncio.create_task(_notify_if_slow(from_number))
    try:
        conversation = await claude_service.get_or_create_conversation(db, from_number)
        response_text = await claude_service.chat(
            db, conversation.id, body, sender_role=role, sender_member=member, images=images
        )
        slow_note.cancel()
        await sms_service.send_sms(to=from_number, body=response_text)
    except Exception as e:
        slow_note.cancel()
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        await sms_service.send_sms(
            to=from_number,
            body="Something went wrong on my end. Please try again in a moment.",
        )


@router.post("/webhook/sms")
async def receive_sms(
    request: Request,
    background: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
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

    if _is_duplicate(MessageSid):
        logger.info(f"Twilio webhook: duplicate delivery {MessageSid} ignored")
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    background.add_task(_process_inbound_bg, From, Body, media)
    return Response(content=TWIML_EMPTY, media_type="application/xml")


@router.post("/webhook/signalhouse")
async def receive_signalhouse_sms(request: Request, background: BackgroundTasks) -> dict:
    """Inbound SMS from Signal House. Register in their dashboard (webhook
    authType NONE) as:
    https://cordia.aigenpartners.com/webhook/signalhouse?secret=<SIGNALHOUSE_WEBHOOK_SECRET>

    Event envelope (per their docs): {timestamp, event: "MESSAGE_RECEIVED",
    identifier, metaData: {Message: {senderPhoneNumber, recipientPhoneNumber,
    messageBody, direction: "INBOUND", ...}}}. Other event types (delivery
    receipts, balance alerts) are acknowledged and ignored."""
    supplied = request.query_params.get("secret") or request.headers.get("X-Webhook-Secret", "")
    if not settings.signalhouse_webhook_secret or not hmac.compare_digest(
        supplied.encode("utf-8"), settings.signalhouse_webhook_secret.encode("utf-8")
    ):
        if not settings.debug:
            logger.warning("Signal House webhook rejected: bad or missing shared secret")
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())
    if not isinstance(payload, dict):
        return {"status": "ignored"}

    event = payload.get("event") or payload.get("eventType") or ""
    if event and event != "MESSAGE_RECEIVED":
        return {"status": "ok", "ignored_event": event}

    message = (payload.get("metaData") or {}).get("Message") or payload
    from_number = message.get("senderPhoneNumber") or message.get("from") or message.get("fromNumber")
    body = message.get("messageBody") or message.get("text") or message.get("body") or ""
    if not from_number or not str(body).strip():
        logger.warning(f"Signal House webhook: unrecognized payload keys {list(payload)[:10]}")
        return {"status": "ignored"}

    # Normalize to +E164 so consent records match across providers
    # (Signal House uses digits-only like '16155551234'; Twilio used '+1615...')
    from_number = str(from_number).strip()
    if not from_number.startswith("+"):
        digits = "".join(ch for ch in from_number if ch.isdigit())
        from_number = f"+{digits}" if len(digits) > 10 else f"+1{digits}"

    # Signal House retries on a slow response; ignore repeat deliveries
    msg_id = str(payload.get("identifier") or message.get("_id") or "")
    if _is_duplicate(msg_id):
        logger.info(f"Signal House webhook: duplicate delivery {msg_id} ignored")
        return {"status": "duplicate_ignored"}

    background.add_task(_process_inbound_bg, from_number, str(body), [])
    return {"status": "ok"}
