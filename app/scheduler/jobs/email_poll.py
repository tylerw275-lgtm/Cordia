"""Poll the assistant's Gmail inbox over IMAP for new replies and process them.

Gmail (free) doesn't push inbound mail to a webhook, so we check every couple
of minutes for unseen messages and route them through the same logic the
webhook uses.
"""
import asyncio
import email as emaillib
import imaplib
import logging
from email.header import decode_header

from app.config import settings
from app.database import get_db_session
from app.services import email_inbound

logger = logging.getLogger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="replace")
        else:
            out += text
    return out


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""


def _fetch_unseen_sync(address: str | None = None, password: str | None = None) -> list[tuple[str, str, str, str]]:
    """Fetch unseen messages from a Gmail inbox over IMAP.

    Returns (uid, sender, subject, body). Deliberately does NOT mark anything
    read — marking happened here, before processing, so any downstream failure
    destroyed the email permanently. Callers mark via mark_seen_sync once the
    message is safely handled. UIDs (not sequence numbers) because those stay
    stable across the two sessions.
    """
    messages: list[tuple[str, str, str, str]] = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as box:
        box.login(address or settings.email_address, password or settings.email_app_password)
        box.select("INBOX")
        typ, data = box.uid("search", None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return messages
        for uid in data[0].split():
            typ, msgdata = box.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            msg = emaillib.message_from_bytes(msgdata[0][1])
            sender = email_inbound.extract_email(msg.get("From"))
            subject = _decode(msg.get("Subject"))
            body = _plain_body(msg)
            messages.append((uid.decode() if isinstance(uid, bytes) else str(uid), sender, subject, body))
    return messages


def mark_seen_sync(uids: list[str], address: str | None = None, password: str | None = None) -> None:
    """Mark messages read, after they have been successfully processed."""
    if not uids:
        return
    with imaplib.IMAP4_SSL("imap.gmail.com") as box:
        box.login(address or settings.email_address, password or settings.email_app_password)
        box.select("INBOX")
        for uid in uids:
            try:
                box.uid("store", uid, "+FLAGS", "\\Seen")
            except Exception as e:
                logger.warning(f"Could not mark message {uid} read: {e}")


async def poll_inbound_email() -> None:
    if not (settings.email_address and settings.email_app_password):
        return
    try:
        messages = await asyncio.to_thread(_fetch_unseen_sync)
    except Exception as e:
        logger.error(f"IMAP poll error: {e}")
        return
    if not messages:
        return
    handled: list[str] = []
    async with get_db_session() as db:
        for uid, sender, subject, body in messages:
            try:
                await email_inbound.process_inbound_email(db, sender, subject, body)
                handled.append(uid)
            except Exception as e:
                # Left unread on purpose so the next poll retries it.
                logger.error(f"Error processing polled email from {email_inbound._mask(sender)}: {e}")
    if handled:
        try:
            await asyncio.to_thread(mark_seen_sync, handled)
        except Exception as e:
            logger.error(f"Could not mark processed email read: {e}")
