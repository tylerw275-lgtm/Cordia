import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import get_db_session
from app.models.real_estate import LeaseReminder
from app.services import family_service, twilio_service

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
        for reminder in reminders:
            if settings.cordia_phone_number:
                await twilio_service.send_sms(to=settings.cordia_phone_number, body=reminder.message)
            reminder.sent = True
            reminder.sent_at = now
        if reminders:
            await db.commit()
            logger.info(f"Sent {len(reminders)} lease reminders")


async def send_birthday_reminders() -> None:
    async with get_db_session() as db:
        upcoming = await family_service.get_upcoming_birthdays(db, days_ahead=7)
        for member in upcoming:
            from datetime import date
            today = date.today()
            bday_this_year = member.birthday.replace(year=today.year)
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
                await twilio_service.send_sms(to=settings.cordia_phone_number, body=msg)
