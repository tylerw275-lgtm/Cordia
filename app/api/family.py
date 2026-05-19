import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import family_service

router = APIRouter(prefix="/api/v1/family", tags=["family"])


@router.get("")
async def list_family(db: AsyncSession = Depends(get_db)) -> dict:
    members = await family_service.list_family_members(db)
    return {
        "family": [
            {
                "id": str(m.id),
                "name": m.name,
                "relationship": m.relationship,
                "city": m.city,
                "state": m.state,
                "birthday": m.birthday.isoformat() if m.birthday else None,
                "interests": m.interests,
            }
            for m in members
        ]
    }


@router.post("")
async def create_family_member(body: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict:
    if "birthday" in body and body["birthday"]:
        body["birthday"] = date.fromisoformat(body["birthday"])
    if "anniversary" in body and body["anniversary"]:
        body["anniversary"] = date.fromisoformat(body["anniversary"])
    member = await family_service.create_family_member(db, **body)
    return {"id": str(member.id), "name": member.name}


@router.get("/{member_id}")
async def get_family_member(member_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    member = await family_service.get_family_member(db, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Family member not found")
    return {
        "id": str(member.id),
        "name": member.name,
        "relationship": member.relationship,
        "city": member.city,
        "state": member.state,
        "birthday": member.birthday.isoformat() if member.birthday else None,
        "anniversary": member.anniversary.isoformat() if member.anniversary else None,
        "email": member.email,
        "phone": member.phone,
        "school_name": member.school_name,
        "grade_level": member.grade_level,
        "interests": member.interests,
        "personality_notes": member.personality_notes,
        "loyalty_programs": member.loyalty_programs,
    }


@router.patch("/{member_id}")
async def update_family_member(
    member_id: uuid.UUID, body: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict:
    if "birthday" in body and body["birthday"]:
        body["birthday"] = date.fromisoformat(body["birthday"])
    if "anniversary" in body and body["anniversary"]:
        body["anniversary"] = date.fromisoformat(body["anniversary"])
    member = await family_service.update_family_member(db, member_id, **body)
    if not member:
        raise HTTPException(status_code=404, detail="Family member not found")
    return {"id": str(member.id), "name": member.name, "updated": True}


@router.get("/events/upcoming")
async def list_events(days_ahead: int = 90, db: AsyncSession = Depends(get_db)) -> dict:
    events = await family_service.list_upcoming_events(db, days_ahead=days_ahead)
    return {
        "events": [
            {"id": str(e.id), "title": e.title, "type": e.event_type, "date": e.event_date.isoformat()}
            for e in events
        ]
    }


@router.post("/events")
async def create_event(body: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict:
    body["event_date"] = date.fromisoformat(body["event_date"])
    event = await family_service.create_family_event(db, **body)
    return {"id": str(event.id), "title": event.title}
