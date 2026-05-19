import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyEvent, FamilyMember


async def get_family_member(db: AsyncSession, member_id: uuid.UUID) -> FamilyMember | None:
    result = await db.execute(select(FamilyMember).where(FamilyMember.id == member_id))
    return result.scalar_one_or_none()


async def get_family_member_by_name(db: AsyncSession, name: str) -> FamilyMember | None:
    from sqlalchemy import func
    result = await db.execute(
        select(FamilyMember).where(func.lower(FamilyMember.name).contains(name.lower()))
    )
    return result.scalars().first()


async def list_family_members(db: AsyncSession) -> Sequence[FamilyMember]:
    result = await db.execute(select(FamilyMember).order_by(FamilyMember.name))
    return result.scalars().all()


async def create_family_member(db: AsyncSession, **kwargs) -> FamilyMember:
    member = FamilyMember(**kwargs)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def update_family_member(db: AsyncSession, member_id: uuid.UUID, **kwargs) -> FamilyMember | None:
    member = await get_family_member(db, member_id)
    if member:
        for key, value in kwargs.items():
            setattr(member, key, value)
        await db.commit()
        await db.refresh(member)
    return member


async def list_upcoming_events(db: AsyncSession, days_ahead: int = 90) -> Sequence[FamilyEvent]:
    from datetime import timedelta
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = await db.execute(
        select(FamilyEvent)
        .where(FamilyEvent.event_date >= today)
        .where(FamilyEvent.event_date <= cutoff)
        .order_by(FamilyEvent.event_date)
    )
    return result.scalars().all()


async def create_family_event(db: AsyncSession, **kwargs) -> FamilyEvent:
    event = FamilyEvent(**kwargs)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def get_upcoming_birthdays(db: AsyncSession, days_ahead: int = 30) -> Sequence[FamilyMember]:
    from datetime import timedelta
    today = date.today()
    members = await list_family_members(db)
    upcoming = []
    for m in members:
        if not m.birthday:
            continue
        this_year = m.birthday.replace(year=today.year)
        if this_year < today:
            this_year = this_year.replace(year=today.year + 1)
        if (this_year - today).days <= days_ahead:
            upcoming.append(m)
    return upcoming
