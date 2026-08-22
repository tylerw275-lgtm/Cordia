import logging

from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.scheduler.jobs.birthday_prep import send_birthday_prep
from app.scheduler.jobs.email_poll import poll_inbound_email
from app.scheduler.jobs.flight_monitor import monitor_flight_prices
from app.scheduler.jobs.history_condense import condense_old_conversations
from app.scheduler.jobs.morning_brief import send_morning_brief
from app.scheduler.jobs.naples_poll import poll_naples_inbox
from app.scheduler.jobs.reminders import send_birthday_reminders, send_lease_reminders

logger = logging.getLogger(__name__)

# Explicit timezone: APScheduler otherwise uses the host's local zone via
# tzlocal, which in a container is UTC — so morning_brief_hour=7, documented as
# a local hour, fired at ~2am Central.
_scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.scheduler_timezone))


def _on_job_error(event) -> None:
    """Surface job crashes in the app's own logs.

    Without this, an exception in a job is logged only by APScheduler's own
    logger and the job just silently doesn't run that day.
    """
    logger.error(f"Scheduled job {event.job_id} failed: {event.exception}", exc_info=event.exception)


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

    _scheduler.add_job(
        send_birthday_prep,
        trigger="cron",
        hour=8,
        minute=15,
        id="birthday_prep",
        replace_existing=True,
    )

    # Two-way email for Gmail: poll the inbox (Gmail has no free inbound webhook)
    if settings.enable_email and settings.email_provider == "gmail" and settings.email_address and settings.email_app_password:
        _scheduler.add_job(
            poll_inbound_email,
            trigger="interval",
            seconds=settings.email_poll_interval_seconds,
            id="email_poll",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Email inbox polling every {settings.email_poll_interval_seconds}s")

    # Naples house inbox: second monitored mailbox for the new property
    if settings.naples_email_address and settings.naples_email_app_password:
        _scheduler.add_job(
            poll_naples_inbox,
            trigger="interval",
            seconds=settings.naples_poll_interval_seconds,
            id="naples_poll",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Naples inbox polling every {settings.naples_poll_interval_seconds}s")

    # Quiet hour: it reads whole conversations and writes summaries, and there
    # is no reason for it to compete with someone actually texting.
    _scheduler.add_job(
        condense_old_conversations,
        trigger="cron",
        hour=settings.history_summary_hour,
        minute=15,
        id="history_condense",
        replace_existing=True,
        max_instances=1,
    )

    _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    _scheduler.start()
    logger.info("Scheduler started")


def shutdown_scheduler() -> None:
    _scheduler.shutdown()
    logger.info("Scheduler stopped")
