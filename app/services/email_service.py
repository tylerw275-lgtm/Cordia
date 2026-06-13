"""Outbound + inbound email for the assistant.

Uses a dedicated, third-party email identity (independent of Crown Bakeries)
so the assistant keeps working even if the company/email changes.
Sending is provider-pluggable; Resend is the default.
"""
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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
    if not settings.enable_email or not settings.email_api_key or not settings.email_from:
        logger.warning("Email not configured (api key / from address missing) — skipping send")
        return {"sent": False, "reason": "email_not_configured"}
    if not to:
        return {"sent": False, "reason": "no_recipient"}

    html = markdown_to_html(body_markdown)

    if settings.email_provider == "resend":
        return await _send_resend(to, subject, html, body_markdown)
    logger.error(f"Unknown email provider: {settings.email_provider}")
    return {"sent": False, "reason": "unknown_provider"}


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
