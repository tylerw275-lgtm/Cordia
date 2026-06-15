"""Shared inbound-email processing, used by both the inbound webhook (Resend)
and the IMAP poller (Gmail). Resolves the sender to Cordia or an opted-in
family member, continues their existing conversation, and replies by email.
"""
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import claude_service, email_service, family_circle_service

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Lines at/after these markers are quoted history from a prior message
_QUOTE_MARKERS = (
    "-----original message-----",
    "________________________________",
)
_ON_WROTE_RE = re.compile(r"^On .+ wrote:$", re.IGNORECASE)


def extract_email(value) -> str:
    if isinstance(value, dict):
        value = value.get("email") or value.get("address") or ""
    m = _EMAIL_RE.search(str(value or ""))
    return m.group(0).lower() if m else ""


def strip_quoted(body: str) -> str:
    """Drop quoted reply history so the model only sees the new message."""
    lines = body.split("\n")
    kept: list[str] = []
    for line in lines:
        low = line.strip().lower()
        if any(low.startswith(m) for m in _QUOTE_MARKERS) or _ON_WROTE_RE.match(line.strip()):
            break
        if line.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip() or body.strip()


async def _resolve_sender(db: AsyncSession, sender: str):
    """Return (role, member, conversation_key)."""
    if settings.owner_email and sender == settings.owner_email.strip().lower():
        return "owner", None, (settings.cordia_phone_number or settings.owner_email)
    member = await family_circle_service.resolve_member_by_email(db, sender)
    if member and member.has_circle_access:
        return "family", member, (member.phone or sender)
    return "unknown", None, None


async def process_inbound_email(db: AsyncSession, sender: str, subject: str, body: str) -> bool:
    sender = (sender or "").strip().lower()
    body = strip_quoted(body or "")
    if not sender or not body:
        return False

    role, member, conv_key = await _resolve_sender(db, sender)
    if role == "unknown":
        logger.warning(f"Inbound email from unknown sender {sender} — ignored")
        return False

    logger.info(f"Inbound email from {sender} (role={role}): {subject[:50]}")
    conversation = await claude_service.get_or_create_conversation(db, conv_key)
    response_text = await claude_service.chat(
        db, conversation.id, body, sender_role=role, sender_member=member
    )
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject or 'your note'}"
    await email_service.send_email(to=sender, subject=reply_subject, body_markdown=response_text)
    return True
