from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyEvent, FamilyMember
from app.services import family_service

TOOL_SCHEMAS = [
    {
        "name": "schedule_family_event",
        "description": (
            "Schedule a family gathering or event, taking into account school calendars and "
            "existing commitments. Also used to capture dates extracted from inbound school "
            "calendars/schedules — save one event per date and link the family members it's for."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Name of the event"},
                "event_type": {
                    "type": "string",
                    "enum": ["gathering", "birthday", "anniversary", "school_event", "holiday"],
                },
                "event_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "notes": {"type": "string", "description": "Any additional notes about the event"},
                "city": {"type": "string", "description": "City where it happens — lets Cordia ask 'what's happening in <city>?'"},
                "family_member_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Family members this event involves, by name",
                },
                "recurrence": {
                    "type": "string",
                    "enum": ["annual", "one_time"],
                    "description": "Whether this event repeats annually",
                },
            },
            "required": ["title", "event_type", "event_date"],
        },
    },
    {
        "name": "list_events_by_location",
        "description": (
            "List upcoming family events in or around a city (e.g. 'what's happening for the "
            "kids in <city>?'). Matches events linked to family members who live there and "
            "events whose notes mention the city. After presenting, offer flight options "
            "around the best dates when relevant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "days_ahead": {"type": "integer", "description": "How far ahead to look (default 120 days)"},
            },
            "required": ["city"],
        },
    },
]


async def schedule_event_handler(db: AsyncSession, **kwargs) -> dict:
    event_date = date.fromisoformat(kwargs.pop("event_date"))
    city = kwargs.pop("city", None)
    member_names = kwargs.pop("family_member_names", None) or []
    member_ids = []
    for name in member_names:
        member = await family_service.get_family_member_by_name(db, name)
        if member:
            member_ids.append(member.id)
    notes = kwargs.pop("notes", None)
    if city:
        notes = f"[{city}] {notes}" if notes else f"[{city}]"
    event = await family_service.create_family_event(
        db, event_date=event_date, notes=notes,
        family_member_ids=member_ids or None, **kwargs,
    )
    return {
        "scheduled": True,
        "event_id": str(event.id),
        "title": event.title,
        "date": event.event_date.isoformat(),
        "linked_members": member_names,
    }


async def list_events_by_location_handler(db: AsyncSession, **kwargs) -> dict:
    city = (kwargs.get("city") or "").strip()
    days_ahead = int(kwargs.get("days_ahead") or 120)
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    # Family members who live in that city
    result = await db.execute(
        select(FamilyMember).where(func.lower(FamilyMember.city).contains(city.lower()))
    )
    local_members = result.scalars().all()
    local_ids = {m.id for m in local_members}

    result = await db.execute(
        select(FamilyEvent)
        .where(FamilyEvent.event_date >= today)
        .where(FamilyEvent.event_date <= cutoff)
        .order_by(FamilyEvent.event_date)
    )
    events = result.scalars().all()

    matched = []
    for e in events:
        linked = set(e.family_member_ids or [])
        in_city_note = city.lower() in (e.notes or "").lower() or city.lower() in e.title.lower()
        if (linked & local_ids) or in_city_note:
            names = []
            for mid in linked:
                m = await family_service.get_family_member(db, mid)
                if m:
                    names.append(m.name)
            matched.append({
                "title": e.title,
                "date": e.event_date.isoformat(),
                "type": e.event_type,
                "who": names,
                "notes": e.notes,
            })
    return {
        "city": city,
        "count": len(matched),
        "events": matched,
        "local_family": [m.name for m in local_members],
        "hint": "If Cordia might want to attend any of these, offer flight options around those dates." if matched else None,
    }
