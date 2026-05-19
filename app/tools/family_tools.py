from sqlalchemy.ext.asyncio import AsyncSession

from app.services import family_service

TOOL_SCHEMAS = [
    {
        "name": "get_family_member",
        "description": "Retrieve a family member's profile including their interests, personality, location, and birthday. Always call this before planning a grandchild trip.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the family member (partial match is fine)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_family_members",
        "description": "List all family members in Cordia's family profile.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_family_events",
        "description": "List upcoming family events, gatherings, birthdays, and anniversaries within a given number of days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 90)",
                    "default": 90,
                },
            },
        },
    },
]


async def get_family_member_handler(db: AsyncSession, name: str, **kwargs) -> dict:
    member = await family_service.get_family_member_by_name(db, name)
    if not member:
        return {"found": False, "message": f"No family member found matching '{name}'"}
    return {
        "found": True,
        "id": str(member.id),
        "name": member.name,
        "relationship": member.relationship,
        "city": member.city,
        "state": member.state,
        "birthday": member.birthday.isoformat() if member.birthday else None,
        "school_name": member.school_name,
        "grade_level": member.grade_level,
        "interests": member.interests or [],
        "personality_notes": member.personality_notes,
        "loyalty_programs": member.loyalty_programs or {},
    }


async def list_family_members_handler(db: AsyncSession, **kwargs) -> dict:
    members = await family_service.list_family_members(db)
    return {
        "family": [
            {
                "name": m.name,
                "relationship": m.relationship,
                "city": m.city,
                "state": m.state,
                "birthday": m.birthday.isoformat() if m.birthday else None,
            }
            for m in members
        ],
        "count": len(members),
    }


async def list_family_events_handler(db: AsyncSession, days_ahead: int = 90, **kwargs) -> dict:
    events = await family_service.list_upcoming_events(db, days_ahead=days_ahead)
    return {
        "events": [
            {
                "title": e.title,
                "type": e.event_type,
                "date": e.event_date.isoformat(),
                "notes": e.notes,
            }
            for e in events
        ],
        "count": len(events),
    }
