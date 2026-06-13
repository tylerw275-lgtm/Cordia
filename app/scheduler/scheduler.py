import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.scheduler.jobs.flight_monitor import monitor_flight_prices
from app.scheduler.jobs.morning_brief import send_morning_brief
from app.scheduler.jobs.reminders import send_birthday_reminders, send_lease_reminders

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


def setup_scheduler() -> None:
    if settings.enable_flight_search:
        _scheduler.add_job(
            monitor_flight_prices,
            trigger="interval",
            minutes=settings.flight_monitor_interval_minutes,
            id="flight_monitor",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Flight monitor scheduled every {settings.flight_monitor_interval_minutes} minutes")

    _scheduler.add_job(
        send_lease_reminders,
        trigger="cron",
        hour=8,
        minute=0,
        id="lease_reminders",
        replace_existing=True,
    )

    _scheduler.add_job(
        send_birthday_reminders,
        trigger="cron",
        hour=7,
        minute=30,
        id="birthday_reminders",
        replace_existing=True,
    )

    _scheduler.add_job(
        send_morning_brief,
        trigger="cron",
        hour=settings.morning_brief_hour,
        minute=45,
        id="morning_brief",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started")


def shutdown_scheduler() -> None:
    _scheduler.shutdown()
    logger.info("Scheduler stopped")
