"""Family circle: lets opted-in family members contribute helpful context
(gift ideas, engagement tips, calendar dates, requests to talk) so Cordia
can connect with them well. Everything here is transparent — designed so
Cordia would be glad it exists.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import (
    FamilyEvent,
    FamilyInput,
    FamilyMember,
    FamilyRequest,
)
from app.utils.phone import normalize_phone


async def resolve_member_by_phone(db: AsyncSession, phone: str) -> FamilyMember | None:
    """Find a family member whose stored phone matches the incoming number."""
    target = normalize_phone(phone)
    if len(target) != 10:
        return None
    result = await db.execute(select(FamilyMember).where(FamilyMember.phone.isnot(None)))
    for member in result.scalars().all():
        if normalize_phone(member.phone) == target:
            return member
    return None


async def resolve_member_by_email(db: AsyncSession, email: str) -> FamilyMember | None:
    """Find a family member whose stored email matches (case-insensitive)."""
    target = (email or "").strip().lower()
    if not target:
        return None
    result = await db.execute(select(FamilyMember).where(FamilyMember.email.isnot(None)))
    for member in result.scalars().all():
        if (member.email or "").strip().lower() == target:
            return member
    return None


async def grant_circle_access(db: AsyncSession, name: str) -> FamilyMember | None:
    from app.services.family_service import get_family_member_by_name
    member = await get_family_member_by_name(db, name)
    if not member:
        return None
    member.has_circle_access = True
    await db.commit()
    await db.refresh(member)
    return member


async def record_consent(db: AsyncSession, member: FamilyMember) -> None:
    if member.circle_consented_at is None:
        member.circle_consented_at = datetime.now(timezone.utc)
        await db.commit()


async def add_input(
    db: AsyncSession,
    from_member_id: uuid.UUID,
    kind: str,
    content: str,
    about_member_id: uuid.UUID | None = None,
) -> FamilyInput:
    item = FamilyInput(
        from_member_id=from_member_id,
        about_member_id=about_member_id,
        kind=kind,
        content=content,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def submit_calendar_event(
    db: AsyncSession,
    from_member_id: uuid.UUID,
    title: str,
    event_date: date,
    notes: str | None = None,
) -> FamilyEvent:
    event = FamilyEvent(
        title=title,
        event_type="family_submitted",
        event_date=event_date,
        notes=notes,
        created_by_member_id=from_member_id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def get_unsurfaced_inputs(db: AsyncSession) -> Sequence[FamilyInput]:
    result = await db.execute(
        select(FamilyInput).where(FamilyInput.surfaced.is_(False)).order_by(FamilyInput.created_at)
    )
    return result.scalars().all()


async def get_inputs_about(db: AsyncSession, member_id, kinds: list[str] | None = None) -> Sequence[FamilyInput]:
    """All inputs family members have shared about a given person (e.g. gift ideas for Bea)."""
    stmt = select(FamilyInput).where(FamilyInput.about_member_id == member_id)
    if kinds:
        stmt = stmt.where(FamilyInput.kind.in_(kinds))
    result = await db.execute(stmt.order_by(FamilyInput.created_at.desc()))
    return result.scalars().all()


async def mark_inputs_surfaced(db: AsyncSession, input_ids: list[uuid.UUID]) -> None:
    if not input_ids:
        return
    result = await db.execute(select(FamilyInput).where(FamilyInput.id.in_(input_ids)))
    for item in result.scalars().all():
        item.surfaced = True
    await db.commit()


async def create_request(
    db: AsyncSession,
    prompt: str,
    about_member_id: uuid.UUID | None = None,
    audience: str = "all",
) -> FamilyRequest:
    req = FamilyRequest(prompt=prompt, about_member_id=about_member_id, audience=audience)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


async def get_open_requests_for(db: AsyncSession, member: FamilyMember) -> Sequence[FamilyRequest]:
    """Open asks this member can help with (audience 'all' or their own id)."""
    result = await db.execute(
        select(FamilyRequest).where(FamilyRequest.fulfilled.is_(False)).order_by(FamilyRequest.created_at)
    )
    reqs = result.scalars().all()
    return [r for r in reqs if r.audience == "all" or r.audience == str(member.id)]


async def fulfill_request(db: AsyncSession, request_id: uuid.UUID) -> None:
    result = await db.execute(select(FamilyRequest).where(FamilyRequest.id == request_id))
    req = result.scalar_one_or_none()
    if req:
        req.fulfilled = True
        await db.commit()


async def get_shared_schedule(db: AsyncSession, days_ahead: int = 180) -> Sequence[FamilyEvent]:
    """Events Cordia has approved for family members to see."""
    from datetime import timedelta
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = await db.execute(
        select(FamilyEvent)
        .where(FamilyEvent.shareable_with_family.is_(True))
        .where(FamilyEvent.event_date >= today)
        .where(FamilyEvent.event_date <= cutoff)
        .order_by(FamilyEvent.event_date)
    )
    return result.scalars().all()
