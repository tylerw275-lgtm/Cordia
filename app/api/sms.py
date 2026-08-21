import asyncio
import hmac
import logging
import random
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, consent_service, family_circle_service, principal_service, sms_service, twilio_service
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
    """Return (role, member). role is 'owner', 'family', 'opted_out',
    'unapproved', or 'unknown'.

    'unapproved' is deliberately distinct from 'unknown': the number is on a
    profile, but Cordia has not approved (or has rejected) it, so it gets no
    conversation. The consent form is publicly linked, so signing it is never
    by itself enough to reach her assistant.

    'opted_out' applies to principals too, which is easy to get wrong. Anyone
    who texted STOP still resolves to a real person, so the turn used to run in
    full and bill for an answer that send_sms then suppressed — she would text,
    pay for the model, and hear nothing.
    """
    status = await consent_service.status_for(db, phone)

    # Principals first: Cordia and anyone who gets their own full assistant.
    principal = await principal_service.resolve_by_phone(db, phone)
    if principal is not None:
        return ("opted_out" if status == "opted_out" else "owner"), principal
    # Config fallback so a deployment that has not set PRINCIPALS_JSON still
    # resolves Cordia rather than rejecting her own number.
    if phones_match(phone, settings.cordia_phone_number) or phones_match(phone, settings.cordia_test_phone_number):
        return ("opted_out" if status == "opted_out" else "owner"), None
    member = await family_circle_service.resolve_member_by_phone(db, phone)
    if member and member.has_circle_access:
        if status == "opted_out":
            return "opted_out", member
        if status in ("rejected", "pending"):
            return "unapproved", member
        return "family", member
    return "unknown", None


async def _record_consent(db: AsyncSession, phone: str, approved: bool = False) -> None:
    """Insert a consent record the first time a number contacts the service.

    `approved` is set when the sender already resolved to someone on Cordia's
    roster — she put them there herself, which is her approval. Numbers that
    arrive any other way stay pending until she says otherwise.
    """
    await db.execute(
        text(
            "INSERT INTO sms_consent (phone, consented_at, method, approval_status) "
            "VALUES (:phone, :ts, 'inbound_text', :status) "
            "ON CONFLICT (phone) DO NOTHING"
        ),
        {"phone": phone, "ts": datetime.now(timezone.utc),
         "status": "approved" if approved else "pending"},
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


# One conversation at a time. Without this every inbound webhook starts its own
# turn immediately, so three texts sent in quick succession become three turns
# running in parallel — each loading history before the others had written to it.
# Cordia saw exactly that: she asked, clarified, then corrected herself, and got
# two separate answers racing each other, one asking questions the other had
# already answered.
_INBOX: "dict[str, list[tuple[str, list]]]" = {}
_INBOX_LOCKS: "dict[str, asyncio.Lock]" = {}


def _conversation_lock(key: str) -> asyncio.Lock:
    lock = _INBOX_LOCKS.get(key)
    if lock is None:
        lock = _INBOX_LOCKS[key] = asyncio.Lock()
    return lock


_MAX_TRACKED_CONVERSATIONS = 500


def _sweep_locks() -> None:
    if len(_INBOX_LOCKS) <= _MAX_TRACKED_CONVERSATIONS:
        return
    for key, lock in list(_INBOX_LOCKS.items()):
        if not lock.locked() and not _INBOX.get(key):
            _INBOX_LOCKS.pop(key, None)


def _merge(batch: list) -> tuple[str, list]:
    """Fold several texts into the single message a person actually meant.

    "What to pack though. Clothes" followed by "It's a second home" is one
    thought split across two taps, not two questions. Answering them separately
    produces two replies that talk past each other.
    """
    body = "\n".join(b for b, _ in batch if b and b.strip())
    media = [m for _, ms in batch for m in (ms or [])]
    return body, media


async def _process_inbound_bg(from_number: str, body: str, media: list) -> None:
    """Background entry point — opens its own DB session (the request-scoped
    one is closed once the webhook responds) and never raises into the app.

    Queues the message and takes the conversation's turn. Whoever holds the lock
    drains everything waiting, so anything that arrives mid-turn joins the next
    one rather than starting a competing turn of its own. Deliberately no early
    "someone else is working" return: the gap between that check and acquiring
    the lock is exactly where a message goes missing.
    """
    from app.database import get_db_session

    _INBOX.setdefault(from_number, []).append((body, media))
    async with _conversation_lock(from_number):
        try:
            while True:
                batch = _INBOX.pop(from_number, [])
                if not batch:
                    break
                merged_body, merged_media = _merge(batch)
                if len(batch) > 1:
                    logger.info(
                        f"Folding {len(batch)} messages from {from_number} into one turn"
                    )
                async with get_db_session() as db:
                    await _process_inbound(db, from_number, merged_body, merged_media)
        except Exception as e:
            logger.error(f"Background inbound processing failed: {e}", exc_info=True)
        finally:
            _INBOX.pop(from_number, None)

    # Keyed by phone number and otherwise immortal. Dropping a lock only once
    # nobody holds or wants it: reaching into its private waiter list to decide
    # would break on any asyncio change, and a bounded sweep is enough.
    _sweep_locks()


# If a reply is still being composed after this many seconds, send a short
# courtesy note so she isn't left wondering whether it landed.
_SLOW_REPLY_SECONDS = 4.0

# Every line here is plain ASCII, and that is a cost decision rather than a style
# one: an em dash or a curly quote is non-GSM, which re-encodes the whole message
# as UCS-2 and drops the segment limit from 160 characters to 70 — doubling the
# carrier cost of a note whose entire job is to be cheap and quick.
_WORKING_NOTES = (
    "Got it - working on this now, one moment.",
    "On it.",
    "Sure - give me a second.",
    "Got it. One moment.",
    "Working on this now.",
    "On it - back in a sec.",
    "Got it, thinking this through.",
)

# For asks that genuinely take a while, say what is actually happening instead of
# a generic hold. Being told "looking this up" reads as work, not as a stall.
_RESEARCH_NOTES = (
    "Looking into this now - give me a minute.",
    "On it. Let me check a few things.",
    "Good one - digging into this now.",
    "Let me look that up properly, one moment.",
    "Checking on this now - won't be long.",
    "On it. This one's worth doing right, so give me a minute.",
)

# Sent when a turn is genuinely long — deep research can run a minute or more,
# and going quiet after one "on it" reads worse than the repetition did.
_STILL_WORKING_NOTES = (
    "Still on it.",
    "Still working on this - almost there.",
    "Not forgotten, still digging.",
    "Still going. Thanks for your patience.",
    "Still on this one - it's taking a bit.",
)

# Seconds to wait before each note, cumulative: 4s, then 34s, then 79s. After
# that Cord goes quiet on purpose — every note is a billable segment, and a
# fourth one is nagging rather than reassuring. The real reply (or the error) is
# what arrives next either way.
_NOTE_SCHEDULE = (4.0, 30.0, 45.0)

# The last few notes sent to each number. Tracking only the single most recent
# one was not enough: across two turns of the same task Cordia saw
# "Still working on this - almost there." twice, because the opening note of the
# second turn had already displaced the follow-up it needed to avoid. A short
# history covers the whole exchange, not just the last message.
_RECENT_NOTES: "OrderedDict[str, list[str]]" = OrderedDict()
_RECENT_NOTES_MAX = 200
_NOTE_MEMORY = 3


def _working_note(to: str, body: str = "", followup: bool = False) -> str:
    """Pick a holding line, matched to the ask and never repeating for a person."""
    from app.prompts.prompt_profiles import is_deep_work

    if followup:
        pool = _STILL_WORKING_NOTES
    else:
        pool = _RESEARCH_NOTES if is_deep_work(body or "") else _WORKING_NOTES
    recent = _RECENT_NOTES.get(to, [])
    choices = [n for n in pool if n not in recent] or list(pool)
    note = random.choice(choices)

    _RECENT_NOTES[to] = (recent + [note])[-_NOTE_MEMORY:]
    _RECENT_NOTES.move_to_end(to)
    while len(_RECENT_NOTES) > _RECENT_NOTES_MAX:
        _RECENT_NOTES.popitem(last=False)
    return note


async def _notify_if_slow(to: str, body: str = "") -> None:
    """Send brief holding notes while a long turn runs.

    Cancelled the moment the real reply is ready, so a fast answer never gets a
    preamble. Beyond the first note the wording switches to "still on it" —
    going quiet for a minute during deep research reads worse than the note
    itself, and a second identical "on it" would read like a stuck machine.

    A failure to send one of these must not kill the task: the reply it is
    covering for is still on its way, and losing that to a courtesy note would
    be a bad trade.
    """
    try:
        for index, delay in enumerate(_NOTE_SCHEDULE):
            await asyncio.sleep(delay)
            try:
                await sms_service.send_sms(
                    to=to, body=_working_note(to, body, followup=index > 0)
                )
            except Exception as e:
                logger.warning(f"Could not send slow-reply note: {e}")
    except asyncio.CancelledError:
        raise


async def _failure_reply(db: AsyncSession, from_number: str) -> str:
    """What to say when a turn dies.

    "Something went wrong, try again" is fine after a one-line question and
    alarming after a five-question interview — she has just spent real effort
    and has no idea whether she is about to be asked all of it again. If there
    is an open project with answers on file, say so.

    Runs inside an except block, so it must never raise: a failure here would
    replace a bad reply with no reply at all.
    """
    generic = "Something went wrong on my end. Please try again in a moment."
    try:
        from sqlalchemy import select

        from app.models.project import Project

        open_project = (await db.execute(
            select(Project)
            .where(Project.status.in_(("intake", "researching")))
            .order_by(Project.updated_at.desc())
            .limit(1)
        )).scalars().first()
        if open_project is None or not open_project.brief:
            return generic
        answered = sum(1 for q in open_project.brief if q.get("answer"))
        if not answered:
            return generic
        return (
            "Something went wrong on my end - but your answers are saved, so you "
            "won't have to go through that again. Just say 'keep going' and I'll "
            "pick up where I left off."
        )
    except Exception:
        return generic


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

    # Re-subscription — but only for somebody who is not already subscribed.
    #
    # "YES" sat in this list unconditionally, so the most common reply in English
    # was intercepted and answered with carrier boilerplate: Cord asked "want me
    # to email you the list?", Cordia said "yes", and got the opt-in
    # confirmation. Every yes/no question was a trap.
    #
    # Gating on the consent record is the compliant reading, not a way around
    # it. Someone genuinely opted out still re-subscribes and still gets the
    # confirmation. Someone already subscribed has nothing to subscribe to, so
    # the word is ordinary conversation. STOP and HELP below stay unconditional
    # — those are the carrier-mandated ones and must work in every state.
    if keyword in ("START", "UNSTOP", "YES"):
        if await consent_service.status_for(db, from_number) in ("opted_out", "no_consent"):
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
    if role == "opted_out":
        # They texted STOP. Running the turn would bill for a reply send_sms
        # then suppresses. The STOP confirmation already said START resumes it,
        # and sending anything else here is the one thing an opt-out forbids.
        logger.warning(
            f"SMS from {from_number} after STOP — no turn run and nothing billed; "
            "only START resumes the program"
        )
        return
    if role == "unapproved":
        # Silence, not an explanation: telling an unapproved number what is
        # gating it would confirm the roster to whoever is probing it.
        logger.warning(
            f"SMS from {from_number} ({member.name}) — consent on file but not "
            "approved by Cordia; ignored"
        )
        return

    # Record consent on first contact. Reaching here means they are on the
    # roster and approved, so the record is created approved.
    await _record_consent(db, from_number, approved=True)

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
    from app.services import usage_service
    # A photo is an MMS and costs ~7x a text, so the media list decides the rate.
    await usage_service.record_sms(
        from_number, body or "", outbound=False, is_mms=bool(media)
    )

    # Download images only now that the sender is authorized (Twilio MMS only)
    images = await twilio_service.fetch_image_blocks(media) if media else []

    slow_note = asyncio.create_task(_notify_if_slow(from_number, body))
    try:
        conversation = await claude_service.get_or_create_conversation(db, from_number)
        response_text = await claude_service.chat(
            db, conversation.id, body, sender_role=role,
            sender_member=member if role == "family" else None,
            sender_user=member if role == "owner" else None,
            images=images,
        )
        slow_note.cancel()
        await sms_service.send_sms(to=from_number, body=response_text)
    except Exception as e:
        slow_note.cancel()
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        # Also record it where it can be seen without shell access. The reply
        # below is identical for every failure, so without this the only trace
        # of what actually broke lives in a log nobody is watching.
        await usage_service.record_error("sms_reply", e, actor=from_number)
        await sms_service.send_sms(to=from_number, body=await _failure_reply(db, from_number))


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
