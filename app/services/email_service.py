"""Outbound + inbound email for the assistant.

Uses a dedicated, third-party email identity (independent of Crown Bakeries)
so the assistant keeps working even if the company/email changes.
Sending is provider-pluggable; Resend is the default.
"""
import asyncio
import logging
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def from_address() -> str:
    """The 'From' header. Prefer an explicit email_from, else derive from the address."""
    if settings.email_from:
        return settings.email_from
    if settings.email_address:
        return f"{settings.email_from_name} <{settings.email_address}>"
    return ""


def markdown_to_html(text: str) -> str:
    """Tiny markdown→HTML for email bodies (headings, bold, lists, line breaks).

    Intentionally minimal — enough to render flight tables, itineraries, and
    summaries cleanly without a heavy dependency.
    """
    lines = text.split("\n")
    html_parts: list[str] = []
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        # bold
        line_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if re.match(r"^#{1,3}\s", line):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            level = len(line) - len(line.lstrip("#"))
            content = line.lstrip("#").strip()
            html_parts.append(f"<h{level}>{content}</h{level}>")
        elif re.match(r"^[-*]\s", line_html):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{line_html[2:].strip()}</li>")
        elif line.strip() == "":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<br>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{line_html}</p>")
    if in_list:
        html_parts.append("</ul>")
    body = "\n".join(html_parts)
    return (
        '<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px;'
        'margin:0 auto;color:#222;line-height:1.5">' + body + "</div>"
    )


async def send_email(to: str, subject: str, body_markdown: str) -> dict:
    """Send an email via the configured provider. Returns {sent: bool, ...}."""
    if not settings.enable_email:
        return {"sent": False, "reason": "email_disabled"}
    if not to:
        return {"sent": False, "reason": "no_recipient"}

    html = markdown_to_html(body_markdown)
    provider = settings.email_provider

    if provider == "gmail":
        if not settings.email_address or not settings.email_app_password:
            logger.warning("Gmail not configured (address / app password missing) — skipping send")
            return {"sent": False, "reason": "email_not_configured"}
        return await _record(await _send_gmail(to, subject, html, body_markdown), to)
    if provider == "resend":
        if not settings.email_api_key or not from_address():
            logger.warning("Resend not configured (api key / from missing) — skipping send")
            return {"sent": False, "reason": "email_not_configured"}
        return await _record(await _send_resend(to, subject, html, body_markdown), to)
    logger.error(f"Unknown email provider: {provider}")
    return {"sent": False, "reason": "unknown_provider"}


async def _record(result: dict, to: str) -> dict:
    """Bill only for mail that actually left. A skipped or failed send is not
    a cost, and counting it would quietly inflate every report."""
    if result.get("sent"):
        from app.services import usage_service
        await usage_service.record_email(to, outbound=True)
    return result


def _send_gmail_sync(to: str, subject: str, html: str, text: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address()
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls(context=context)
        server.login(settings.email_address, settings.email_app_password)
        server.sendmail(settings.email_address, [to], msg.as_string())


async def _send_gmail(to: str, subject: str, html: str, text: str) -> dict:
    try:
        await asyncio.to_thread(_send_gmail_sync, to, subject, html, text)
        return {"sent": True}
    except Exception as e:
        logger.error(f"Gmail send_email error: {e}")
        return {"sent": False, "reason": str(e)}


async def _send_resend(to: str, subject: str, html: str, text: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.email_api_key}"},
                json={
                    "from": settings.email_from,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {"sent": True, "id": data.get("id")}
    except Exception as e:
        logger.error(f"Resend send_email error: {e}")
        return {"sent": False, "reason": str(e)}


class ReceivedEmailUnavailable(RuntimeError):
    """The message metadata arrived but its content could not be fetched.

    Raised rather than returned so the webhook can answer 500 and let Resend
    retry. Returning an empty body instead would look identical to a genuinely
    empty message and the reply would be dropped silently — which is exactly
    the bug this fetch exists to fix.
    """


async def fetch_received_email(email_id: str) -> dict:
    """Fetch one inbound message's content from Resend.

    Resend's `email.received` webhook deliberately carries metadata only —
    `email_id`, `from`, `to`, `subject` — so the body has to be fetched by id.
    Returns the decoded payload; `text` holds the plain body, `html` the rich
    one (either may be null, never both).
    """
    if not settings.email_api_key:
        raise ReceivedEmailUnavailable("EMAIL_API_KEY is not set")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}",
                headers={"Authorization": f"Bearer {settings.email_api_key}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Could not fetch received email {email_id}: {e}")
        raise ReceivedEmailUnavailable(str(e)) from e
