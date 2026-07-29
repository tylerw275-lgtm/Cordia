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


def _fetch_unseen_sync(address: str | None = None, password: str | None = None) -> list[tuple[str, str, str]]:
    """Fetch unseen messages from a Gmail inbox over IMAP. Defaults to the
    assistant's main mailbox; the Naples house poller passes its own creds."""
    messages: list[tuple[str, str, str]] = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as box:
        box.login(address or settings.email_address, password or settings.email_app_password)
        box.select("INBOX")
        typ, data = box.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return messages
        for num in data[0].split():
            typ, msgdata = box.fetch(num, "(RFC822)")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            msg = emaillib.message_from_bytes(msgdata[0][1])
            sender = email_inbound.extract_email(msg.get("From"))
            subject = _decode(msg.get("Subject"))
            body = _plain_body(msg)
            messages.append((sender, subject, body))
            box.store(num, "+FLAGS", "\\Seen")
    return messages


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
    async with get_db_session() as db:
        for sender, subject, body in messages:
            try:
                await email_inbound.process_inbound_email(db, sender, subject, body)
            except Exception as e:
                logger.error(f"Error processing polled email from {sender}: {e}")
