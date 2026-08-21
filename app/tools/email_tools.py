"""Owner tool: send a longer, formatted artifact to the principal's email when
SMS is too small (flight comparisons, itineraries, lease summaries, gift
roundups).

It goes to whoever asked for it. That sounds obvious, but this tool used to
hardcode the account holder's address, which meant a report Tom or Karie asked
for was mailed to Cordia and never to them — in the same release that walled
their workspaces off from each other.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.services import email_service
from app.tools.actor import wants_actor

OWNER_TOOL_SCHEMAS = [
    {
        "name": "send_report_email",
        "description": (
            "Email the person you are talking to a longer, formatted document when the content "
            "is too detailed for SMS — "
            "flight comparisons, trip itineraries, lease summaries, or curated lists. "
            "After calling this, send a short 1-2 sentence text summary pointing to the email. "
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


async def send_report_email_handler(
    db: AsyncSession, acting_user: AuthorizedUser | None = None, **kw
) -> dict:
    # The requester's own address, falling back to the configured owner only
    # when there is no principal on the turn at all.
    to = (acting_user.email if acting_user else None) or settings.owner_email
    who = acting_user.name.split()[0] if acting_user else "Cordia"
    if not to:
        return {
            "sent": False,
            "message": f"No email on file for {who} yet — ask for the best address to use.",
        }
    result = await email_service.send_email(
        to=to, subject=kw["subject"], body_markdown=kw["body_markdown"]
    )
    if result.get("sent"):
        return {
            "sent": True, "to": to, "subject": kw["subject"],
            "message": "Email sent — now send a short SMS summary pointing to it.",
        }
    return {"sent": False, "reason": result.get("reason"),
            "message": "Email could not be sent right now."}


OWNER_EMAIL_HANDLERS = wants_actor({
    "send_report_email": send_report_email_handler,
})
