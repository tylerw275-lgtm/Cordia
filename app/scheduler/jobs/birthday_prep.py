"""Proactive birthday prep — ~2 weeks before a family birthday, text Cordia,
surface any gift ideas the family has shared, and offer to plan or source more.

The outgoing message is recorded in Cordia's conversation so when she replies
("yes, ask them"), the assistant has the full context to act on it.
"""
import logging
from datetime import date

from app.config import settings
from app.database import get_db_session
from app.services import claude_service, family_circle_service, family_service, sms_service

logger = logging.getLogger(__name__)


def _humanize(days: int) -> str:
    if days % 7 == 0:
        weeks = days // 7
        return "1 week" if weeks == 1 else f"{weeks} weeks"
    return f"{days} days"


async def compose_birthday_prep(db, member, days_away: int) -> str:
    first = member.nickname or member.name.split()[0]
    today = date.today()
    try:
        bday = member.birthday.replace(year=today.year)
        if bday < today:
            bday = bday.replace(year=today.year + 1)
    except ValueError:
        bday = member.birthday
    date_str = bday.strftime("%B %-d") if hasattr(bday, "strftime") else str(bday)
    when = _humanize(days_away)

    ideas = await family_circle_service.get_inputs_about(
        db, member.id, kinds=["gift_idea", "engagement_tip"]
    )
    if ideas:
        idea_txt = "; ".join(i.content for i in ideas[:3])
        return (
            f"Heads up — {first}'s birthday is in {when} ({date_str}). "
            f"The family's already shared some ideas: {idea_txt}. "
            f"Want me to pull together gift options or help plan something?"
        )
    return (
        f"Heads up — {first}'s birthday is in {when} ({date_str}). "
        f"Want me to ask the family for gift ideas and put together some options? "
        f"I can also suggest a few based on what {first} loves."
    )


async def send_birthday_prep() -> None:
    if not settings.cordia_phone_number:
        return
    lead = settings.birthday_prep_lead_days
    async with get_db_session() as db:
        today = date.today()
        members = await family_service.list_family_members(db)
        for m in members:
            if not m.birthday:
                continue
            try:
                bday = m.birthday.replace(year=today.year)
            except ValueError:
                continue
            if bday < today:
                try:
                    bday = bday.replace(year=today.year + 1)
                except ValueError:
                    continue
            if (bday - today).days != lead:
                continue
            msg = await compose_birthday_prep(db, m, lead)
            await sms_service.send_sms(to=settings.cordia_phone_number, body=msg)
            # Record so Cordia's reply ("yes, ask them") has context
            await claude_service.record_assistant_message(db, settings.cordia_phone_number, msg)
            logger.info(f"Sent birthday prep for {m.name}")
