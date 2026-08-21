"""Proactive daily brief — a short morning text so the assistant feels alive:
today's events, upcoming birthdays, flight price drops, and anything new from family.
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.config import settings
from app.database import get_db_session
from app.models.trips import FlightWatch, PriceSnapshot
from app.services import claude_service, family_circle_service, family_service, sms_service

logger = logging.getLogger(__name__)


async def _recent_price_drops(db) -> list[str]:
    lines = []
    result = await db.execute(select(FlightWatch).where(FlightWatch.is_active == True))  # noqa: E712
    for watch in result.scalars().all():
        snaps = await db.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.flight_watch_id == watch.id)
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )
        snap = snaps.scalars().first()
        if snap and snap.alerted:
            lines.append(f"{watch.origin}->{watch.destination} now ${float(snap.lowest_price or 0):.0f}")
    return lines


async def compose_brief(db) -> str | None:
    today = date.today()
    parts: list[str] = [f"Good morning! Here's your day ({today.strftime('%a %b %d')}):"]

    events = await family_service.list_upcoming_events(db, days_ahead=1)
    todays = [e for e in events if e.event_date == today]
    if todays:
        parts.append("Today: " + "; ".join(e.title for e in todays))

    birthdays = await family_service.get_upcoming_birthdays(db, days_ahead=14)
    if birthdays:
        bday_strs = []
        for m in birthdays:
            try:
                d = m.birthday.replace(year=today.year)
            except ValueError:
                continue
            bday_strs.append(f"{m.nickname or m.name.split()[0]} ({d.strftime('%b %d')})")
        if bday_strs:
            parts.append("Birthdays soon: " + ", ".join(bday_strs))

    drops = await _recent_price_drops(db)
    if drops:
        parts.append("Fare drops: " + "; ".join(drops))

    pending = await family_circle_service.get_unsurfaced_inputs(db)
    if pending:
        parts.append(f"{len(pending)} new note(s) from family — text me to hear them.")

    # If nothing notable beyond the greeting, skip the brief entirely
    if len(parts) == 1:
        logger.info("Morning brief: nothing to report today, not sending")
        return None
    return "\n".join(parts)


async def send_morning_brief() -> None:
    if not settings.cordia_phone_number:
        logger.error("Morning brief skipped: CORDIA_PHONE_NUMBER is not configured")
        return
    async with get_db_session() as db:
        brief = await compose_brief(db)
        if not brief:
            return
        if await sms_service.send_sms(to=settings.cordia_phone_number, body=brief):
            logger.info("Sent morning brief")
            # So a reply ("book it", "tell me more") has the brief as context.
            await claude_service.record_assistant_message(db, settings.cordia_phone_number, brief)
