import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import get_db_session
from app.models.real_estate import LeaseReminder
from app.services import claude_service, family_service, sms_service

logger = logging.getLogger(__name__)


async def send_lease_reminders() -> None:
    async with get_db_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(LeaseReminder)
            .where(LeaseReminder.sent == False)
            .where(LeaseReminder.remind_at <= now)
        )
        reminders = result.scalars().all()
        if reminders and not settings.cordia_phone_number:
            logger.error(
                f"{len(reminders)} lease reminder(s) due but CORDIA_PHONE_NUMBER is unset — "
                "leaving them unsent rather than marking them delivered"
            )
            return
        sent = 0
        for reminder in reminders:
            # Mark sent only if it actually went out: this used to be
            # unconditional, so with no phone configured every due reminder was
            # consumed and logged as delivered.
            if await sms_service.send_sms(to=settings.cordia_phone_number, body=reminder.message):
                reminder.sent = True
                reminder.sent_at = now
                sent += 1
        if reminders:
            await db.commit()
            logger.info(f"Sent {sent} of {len(reminders)} due lease reminders")


async def send_birthday_reminders() -> None:
    async with get_db_session() as db:
        upcoming = await family_service.get_upcoming_birthdays(db, days_ahead=7)
        for member in upcoming:
            from datetime import date
            today = date.today()
            # Recomputing replace(year=today.year) here discarded the rollover
            # get_upcoming_birthdays already performed, so a birthday early in
            # January evaluated in December came out ~-358 days and was skipped.
            bday_this_year = family_service.next_birthday(member.birthday, today)
            if bday_this_year is None:
                continue
            days_away = (bday_this_year - today).days
            if days_away == 7:
                msg = f"Reminder: {member.name}'s birthday is in 1 week ({bday_this_year.strftime('%B %d')})."
            elif days_away == 1:
                msg = f"Tomorrow is {member.name}'s birthday! ({bday_this_year.strftime('%B %d')})"
            elif days_away == 0:
                msg = f"Today is {member.name}'s birthday! Happy birthday to them."
            else:
                continue

            if settings.cordia_phone_number:
                if await sms_service.send_sms(to=settings.cordia_phone_number, body=msg):
                    await claude_service.record_assistant_message(
                        db, settings.cordia_phone_number, msg
                    )
