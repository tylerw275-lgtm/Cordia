"""Owner tool: send a longer, formatted artifact to Cordia's email when SMS
is too small (flight comparisons, itineraries, lease summaries, gift roundups).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import email_service

OWNER_TOOL_SCHEMAS = [
    {
        "name": "send_report_email",
        "description": (
            "Email Cordia a longer, formatted document when the content is too detailed for SMS — "
            "flight comparisons, trip itineraries, lease summaries, or curated lists. "
            "After calling this, send her a short 1-2 sentence text summary pointing to the email. "
            "Use Markdown in the body (## headings, **bold**, - bullets)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "body_markdown": {"type": "string", "description": "The full report body in Markdown"},
            },
            "required": ["subject", "body_markdown"],
        },
    },
]


async def send_report_email_handler(db: AsyncSession, **kw) -> dict:
    to = settings.owner_email
    if not to:
        return {"sent": False, "message": "No email on file for Cordia yet — ask her for the best address to use."}
    result = await email_service.send_email(to=to, subject=kw["subject"], body_markdown=kw["body_markdown"])
    if result.get("sent"):
        return {"sent": True, "to": to, "subject": kw["subject"], "message": "Email sent — now give her a short SMS summary."}
    return {"sent": False, "reason": result.get("reason"), "message": "Email could not be sent right now."}


OWNER_EMAIL_HANDLERS = {
    "send_report_email": send_report_email_handler,
}
