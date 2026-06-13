"""Inbound email webhook — gives the assistant true two-way email.

Cordia (or an opted-in family member) can reply to an email from the
assistant and the conversation continues, sharing the same memory and
thread as their SMS. Provider-agnostic: accepts JSON or form posts and
extracts sender / subject / body leniently.
"""
import logging
import re

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, email_service, family_circle_service

logger = logging.getLogger(__name__)
router = APIRouter()

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract_email(value) -> str:
    if isinstance(value, dict):
        value = value.get("email") or value.get("address") or ""
    m = _EMAIL_RE.search(str(value or ""))
    return m.group(0).lower() if m else ""


def _first(payload: dict, *keys) -> str:
    for k in keys:
        if payload.get(k):
            return str(payload[k])
    return ""


async def _parse_inbound(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        data = await request.json()
        # Some providers nest under "data"
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = {**data, **data["data"]}
        return data if isinstance(data, dict) else {}
    form = dict(await request.form())
    return form


@router.post("/webhook/email")
async def receive_email(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    # Optional shared-secret check (provider configured to include ?secret=...)
    if settings.email_inbound_secret:
        if request.query_params.get("secret") != settings.email_inbound_secret:
            logger.warning("Inbound email rejected — bad secret")
            return Response(status_code=403)

    payload = await _parse_inbound(request)
    sender = _extract_email(payload.get("from") or payload.get("sender") or payload.get("From"))
    subject = _first(payload, "subject", "Subject")
    body = _first(payload, "text", "plain", "body-plain", "stripped-text", "TextBody", "body").strip()

    if not sender or not body:
        return Response(status_code=200)

    # Resolve sender: Cordia (owner) or an opted-in family member
    role = "unknown"
    member = None
    conv_key = None
    reply_to = sender
    if settings.owner_email and sender == settings.owner_email.strip().lower():
        role = "owner"
        conv_key = settings.cordia_phone_number or settings.owner_email
    else:
        member = await family_circle_service.resolve_member_by_email(db, sender)
        if member and member.has_circle_access:
            role = "family"
            conv_key = member.phone or sender

    if role == "unknown":
        logger.warning(f"Inbound email from unknown sender {sender} — ignored")
        return Response(status_code=200)

    logger.info(f"Inbound email from {sender} (role={role}): {subject[:50]}")

    try:
        conversation = await claude_service.get_or_create_conversation(db, conv_key)
        response_text = await claude_service.chat(
            db, conversation.id, body, sender_role=role, sender_member=member
        )
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject or 'your note'}"
        await email_service.send_email(to=reply_to, subject=reply_subject, body_markdown=response_text)
    except Exception as e:
        logger.error(f"Error processing inbound email: {e}", exc_info=True)

    return Response(status_code=200)
