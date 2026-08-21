"""Outbound drafting & approval engine (owner-only).

The only path by which Cord sends anything to a third party. Invariants:
- Drafts never send themselves. send_outbound requires an approval code that
  is generated server-side at draft time and must appear in one of Cordia's own
  inbound messages — a boolean the model sets itself is not an approval,
  because anything that can call the tool can also set the boolean.
- SMS goes only to numbers with a live consent record (opted in, not opted
  out); everyone else is emailed or skipped with a report.
- Real addresses are resolved server-side at draft time and never returned
  to the model.
"""
import hmac
import logging
import secrets

from app.utils.phone import normalize_phone
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbound import OutboundMessage
from app.services import email_service, sms_service
from app.tools.contact_tools import resolve_recipient

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "name": "create_outbound_drafts",
        "description": (
            "Store personalized outbound message drafts — one per recipient — for Cordia to "
            "review. Write each body yourself, personalized to that person/family (never a "
            "generic blast). This does NOT send anything. After calling, show Cordia a compact "
            "review (recipient + one-line preview) and ask for her approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short label for this batch (e.g. 'st-thomas-departure')"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "to_name": {"type": "string", "description": "Recipient's name — the address is looked up securely from the contact book"},
                            "channel": {"type": "string", "enum": ["email", "sms"], "description": "Prefer email; SMS only for opted-in numbers"},
                            "subject": {"type": "string", "description": "Email subject (email only)"},
                            "body": {"type": "string", "description": "The full personalized message, written in a warm voice on Cordia's behalf"},
                        },
                        "required": ["to_name", "channel", "body"],
                    },
                },
            },
            "required": ["messages"],
        },
    },
    {
        "name": "review_outbound_drafts",
        "description": "List the drafts in a batch (recipient, channel, subject, full body, status) so Cordia can review or you can re-check before sending.",
        "input_schema": {
            "type": "object",
            "properties": {"batch_id": {"type": "string", "description": "Batch to review; omit for the most recent batch"}},
        },
    },
    {
        "name": "edit_outbound_draft",
        "description": "Rewrite one draft's body (and/or subject) after Cordia asks for changes. Re-confirm with her before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "to_name": {"type": "string", "description": "Which recipient's draft to edit"},
                "new_body": {"type": "string"},
                "new_subject": {"type": "string"},
            },
            "required": ["to_name", "new_body"],
        },
    },
    {
        "name": "cancel_outbound",
        "description": "Cancel a batch of drafts (or one recipient's draft) that Cordia decided not to send.",
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "to_name": {"type": "string", "description": "Cancel only this recipient's draft (optional)"},
            },
        },
    },
    {
        "name": "send_outbound",
        "description": (
            "Send the approved drafts in a batch. ONLY call this after Cordia has explicitly "
            "approved sending in this conversation (e.g. 'send them'). Never call it on your "
            "own initiative or on a third party's request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string", "description": "Batch to send; omit for the most recent batch"},
                "approval_code": {"type": "string", "description": "The code Cordia texted back to approve this batch. You cannot supply this yourself — she must send it."},
            },
            "required": ["approval_code"],
        },
    },
]


def mask_address(addr: str | None) -> str:
    """Log-safe masking: 'k****@g****.com', '+1*******1234'."""
    if not addr:
        return "(none)"
    if "@" in addr:
        local, _, domain = addr.partition("@")
        return f"{local[:1]}****@{domain[:1]}****{domain[domain.rfind('.') :] if '.' in domain else ''}"
    return f"{addr[:2]}*****{addr[-4:]}" if len(addr) > 6 else "***"


async def _sms_consent_ok(db: AsyncSession, phone: str) -> bool:
    result = await db.execute(
        # Match on the last 10 digits: consent rows are always +E164, but family
        # profiles carry bare digits, so an opted-in son read as "not opted in"
        # and his drafts were silently rerouted to email.
        text(
            "SELECT 1 FROM sms_consent "
            "WHERE right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) = :digits "
            "AND consented_at IS NOT NULL AND opted_out_at IS NULL"
        ),
        {"digits": normalize_phone(phone)},
    )
    return result.first() is not None


async def _latest_batch_id(db: AsyncSession) -> str | None:
    result = await db.execute(
        select(OutboundMessage.batch_id).order_by(OutboundMessage.created_at.desc()).limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def _batch_drafts(db: AsyncSession, batch_id: str) -> list[OutboundMessage]:
    result = await db.execute(
        select(OutboundMessage).where(OutboundMessage.batch_id == batch_id).order_by(OutboundMessage.created_at)
    )
    return list(result.scalars().all())


async def create_outbound_drafts_handler(db: AsyncSession, **kw) -> dict:
    topic = (kw.get("topic") or "batch").strip().lower().replace(" ", "-")[:20]
    batch_id = f"{topic}-{uuid.uuid4().hex[:8]}"
    # Server-side approval code. Cordia has to send this back before anything goes out.
    approval_code = f"{secrets.randbelow(1000000):06d}"
    drafted, problems = [], []
    for m in kw.get("messages", []):
        recipient = await resolve_recipient(db, m["to_name"])
        channel = m.get("channel", "email")
        address = recipient["email"] if channel == "email" else recipient["phone"]
        if not address:
            # Fall back to the other channel if we have that address instead
            alt = recipient["phone"] if channel == "email" else recipient["email"]
            if alt and channel == "sms" and recipient["email"]:
                channel, address = "email", recipient["email"]
            elif alt and channel == "email" and recipient["phone"]:
                channel, address = "sms", recipient["phone"]
            else:
                problems.append({"to_name": m["to_name"], "issue": "no_address",
                                 "fix": "Ask Cordia for their email or phone, save with add_contact, then redraft."})
                continue
        if channel == "sms" and not await _sms_consent_ok(db, address):
            if recipient["email"]:
                channel, address = "email", recipient["email"]
            else:
                problems.append({"to_name": m["to_name"], "issue": "sms_not_opted_in_and_no_email",
                                 "fix": "This number hasn't opted in to texts. Ask Cordia for an email address instead."})
                continue
        draft = OutboundMessage(
            batch_id=batch_id, channel=channel, to_name=recipient["name"],
            to_address=address, subject=m.get("subject"), body=m["body"],
            approval_code=approval_code,
        )
        db.add(draft)
        drafted.append({"to_name": recipient["name"], "channel": channel,
                        "subject": m.get("subject"), "preview": m["body"][:120]})
    await db.commit()
    return {
        "batch_id": batch_id,
        "drafted": drafted,
        "problems": problems,
        "status": "drafts_stored_NOT_sent",
        "approval_code": approval_code,
        "message": (
            "Show Cordia the recipients and previews, resolve any problems, then ask her to "
            f"reply with the code {approval_code} to approve. Nothing sends until she sends "
            "that code herself — you cannot approve this on her behalf."
        ),
    }


async def review_outbound_drafts_handler(db: AsyncSession, **kw) -> dict:
    batch_id = kw.get("batch_id") or await _latest_batch_id(db)
    if not batch_id:
        return {"drafts": [], "message": "No draft batches on file."}
    drafts = await _batch_drafts(db, batch_id)
    return {
        "batch_id": batch_id,
        "drafts": [
            {"to_name": d.to_name, "channel": d.channel, "subject": d.subject,
             "body": d.body, "status": d.status}
            for d in drafts
        ],
    }


async def edit_outbound_draft_handler(db: AsyncSession, **kw) -> dict:
    batch_id = kw.get("batch_id") or await _latest_batch_id(db)
    if not batch_id:
        return {"edited": False, "message": "No draft batches on file."}
    target = (kw["to_name"] or "").strip().lower()
    for d in await _batch_drafts(db, batch_id):
        if d.status == "draft" and target in d.to_name.lower():
            d.body = kw["new_body"]
            if kw.get("new_subject"):
                d.subject = kw["new_subject"]
            await db.commit()
            return {"edited": True, "to_name": d.to_name, "message": "Updated — re-confirm with Cordia before sending."}
    return {"edited": False, "message": f"No editable draft for {kw['to_name']} in batch {batch_id}."}


async def cancel_outbound_handler(db: AsyncSession, **kw) -> dict:
    batch_id = kw.get("batch_id") or await _latest_batch_id(db)
    if not batch_id:
        return {"canceled": 0}
    target = (kw.get("to_name") or "").strip().lower()
    canceled = 0
    for d in await _batch_drafts(db, batch_id):
        if d.status == "draft" and (not target or target in d.to_name.lower()):
            d.status = "canceled"
            canceled += 1
    await db.commit()
    return {"canceled": canceled, "batch_id": batch_id}


async def _code_confirmed_by_owner(db: AsyncSession, code: str) -> bool:
    """True if the code appears in a message Cordia actually sent.

    Checked against persisted inbound user turns rather than trusting the tool
    input, so a model that merely knows the code still cannot approve on her
    behalf — she has to have typed it.
    """
    from app.models.conversation import Message

    if not code:
        return False
    result = await db.execute(
        select(Message)
        .where(Message.role == "user")
        .where(Message.content.contains(code))
        .limit(1)
    )
    return result.scalars().first() is not None


async def send_outbound_handler(db: AsyncSession, **kw) -> dict:
    batch_id = kw.get("batch_id") or await _latest_batch_id(db)
    if not batch_id:
        return {"sent": 0, "message": "No draft batches on file."}

    drafts = await _batch_drafts(db, batch_id)
    expected = next((d.approval_code for d in drafts if d.approval_code), None)
    supplied = (kw.get("approval_code") or "").strip()

    if not expected:
        return {"sent": 0, "blocked": True,
                "message": "This batch has no approval code and cannot be sent. Redraft it."}
    if not hmac.compare_digest(supplied, expected):
        return {"sent": 0, "blocked": True,
                "message": "Not sent — that approval code doesn't match this batch. Ask Cordia to text the code back."}
    if not await _code_confirmed_by_owner(db, expected):
        return {"sent": 0, "blocked": True,
                "message": ("Not sent — Cordia hasn't sent the approval code yet. Show her the drafts "
                            "and ask her to reply with the code. Do not send it on her behalf.")}
    sent, skipped = [], []
    for d in drafts:
        if d.status != "draft":
            continue
        try:
            if d.channel == "email":
                result = await email_service.send_email(
                    to=d.to_address, subject=d.subject or "A note from Cordia", body_markdown=d.body
                )
                ok, reason = bool(result.get("sent")), result.get("reason")
            else:  # sms — re-check consent at send time
                if not await _sms_consent_ok(db, d.to_address):
                    ok, reason = False, "sms_consent_missing"
                else:
                    await sms_service.send_sms(to=d.to_address, body=d.body)
                    ok, reason = True, None
        except Exception as e:
            ok, reason = False, str(e)
        if ok:
            d.status = "sent"
            d.sent_at = datetime.now(timezone.utc)
            sent.append({"to_name": d.to_name, "channel": d.channel})
            logger.info(f"Outbound sent to {d.to_name} via {d.channel} ({mask_address(d.to_address)})")
        else:
            d.status = "skipped"
            d.error = reason
            skipped.append({"to_name": d.to_name, "channel": d.channel, "reason": reason})
            logger.warning(f"Outbound skipped for {d.to_name} ({mask_address(d.to_address)}): {reason}")
    await db.commit()
    return {"batch_id": batch_id, "sent": sent, "skipped": skipped,
            "message": "Report to Cordia what went out and anything skipped."}


HANDLERS = {
    "create_outbound_drafts": create_outbound_drafts_handler,
    "review_outbound_drafts": review_outbound_drafts_handler,
    "edit_outbound_draft": edit_outbound_draft_handler,
    "cancel_outbound": cancel_outbound_handler,
    "send_outbound": send_outbound_handler,
}
