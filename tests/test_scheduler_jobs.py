"""Scheduled jobs must not fail silently or consume work they didn't do."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.real_estate import Lease, LeaseReminder
from app.scheduler.jobs import reminders
from app.services import family_service


@pytest.mark.asyncio
async def test_lease_reminders_are_not_consumed_when_no_phone_is_configured(db, mocker):
    """`sent = True` sat outside the phone guard, so with CORDIA_PHONE_NUMBER
    unset every due reminder was marked delivered and lost."""
    lease = Lease(
        property_address="1 Test St",
        monthly_rent=1000,
        lease_start=date.today(),
        lease_end=date.today() + timedelta(days=365),
    )
    db.add(lease)
    await db.commit()
    await db.refresh(lease)

    reminder = LeaseReminder(
        lease_id=lease.id,
        message="Renewal deadline in 30 days",
        remind_at=datetime.now(timezone.utc) - timedelta(hours=1),
        sent=False,
    )
    db.add(reminder)
    await db.commit()

    mocker.patch("app.config.settings.cordia_phone_number", "")
    mocker.patch("app.scheduler.jobs.reminders.get_db_session", _session_ctx(db))

    await reminders.send_lease_reminders()

    await db.refresh(reminder)
    assert reminder.sent is False
    assert reminder.sent_at is None


def _session_ctx(db):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield db

    return _ctx


# ---------------------------------------------------------------------------
# Birthday rollover
# ---------------------------------------------------------------------------

def test_next_birthday_rolls_into_next_year():
    # Evaluated in late December, a mid-January birthday is 25 days away —
    # the old code computed replace(year=today.year) and got about -340.
    assert family_service.next_birthday(date(1986, 1, 14), date(2026, 12, 20)) == date(2027, 1, 14)


def test_next_birthday_today_is_today():
    assert family_service.next_birthday(date(1986, 6, 1), date(2026, 6, 1)) == date(2026, 6, 1)


def test_leap_day_birthday_falls_back_to_feb_28():
    """A Feb-29 birthday used to hit ValueError and be dropped entirely, so it
    never produced a reminder in a common year."""
    assert family_service.next_birthday(date(2000, 2, 29), date(2026, 1, 1)) == date(2026, 2, 28)
    # In a leap year it keeps the real date.
    assert family_service.next_birthday(date(2000, 2, 29), date(2028, 1, 1)) == date(2028, 2, 29)


# ---------------------------------------------------------------------------
# Scheduler configuration
# ---------------------------------------------------------------------------

def test_scheduler_uses_the_configured_timezone():
    """APScheduler defaults to the host zone (UTC in a container), so a
    'local hour' of 7 fired at ~2am Central."""
    from app.config import settings
    from app.scheduler import scheduler as sched

    assert settings.scheduler_timezone == "America/Chicago"
    assert str(sched._scheduler.timezone) == settings.scheduler_timezone


@pytest.mark.asyncio
async def test_scheduler_reports_job_errors():
    from apscheduler.events import EVENT_JOB_ERROR

    from app.scheduler import scheduler as sched

    sched.setup_scheduler()
    try:
        listeners = [cb for cb, mask in sched._scheduler._listeners if mask & EVENT_JOB_ERROR]
        assert listeners, "no EVENT_JOB_ERROR listener attached"
    finally:
        sched.shutdown_scheduler()
