from sqlalchemy.ext.asyncio import AsyncSession

from app.services import family_service

TOOL_SCHEMAS = [
    {
        "name": "get_family_member",
        "description": "Retrieve a family member's profile including interests, personality, location and birthday. Always call this before planning a grandchild trip. The lookup also succeeds if Cordia uses an old name for someone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the family member (partial match, nicknames, and former names all work)",
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
    {
        "name": "get_grandkid_activity_balance",
        "description": "Check whether Cordia's special activities are balanced between grandsons and granddaughters. Call this whenever a grandkid trip or activity is being discussed, or when she mentions having done something with grandkids.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "log_grandkid_activity",
        "description": "Record a special activity Cordia did with grandkids. Call this when she mentions having done something fun or memorable with one or more grandchildren.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Name of the activity or event"},
                "activity_date": {"type": "string", "description": "Date in YYYY-MM-DD format (approximate is fine)"},
                "category": {
                    "type": "string",
                    "enum": ["concert", "travel", "theme_park", "sports", "cultural", "shopping", "restaurant", "outdoor", "other"],
                },
                "participant_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of grandkids who participated",
                },
                "notes": {"type": "string", "description": "Any additional context"},
            },
            "required": ["title", "activity_date", "category", "participant_names"],
        },
    },
    {
        "name": "add_family_member",
        "description": "Add a NEW person to Cordia's family profiles — a new grandchild, a new in-law, someone not already in the FAMILY ROSTER. Check the roster first; use update_family_member_notes for someone already there.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "relationship": {"type": "string", "description": "e.g. grandson, granddaughter, daughter-in-law, in-law"},
                "nickname": {"type": "string", "description": "What the family calls them, if different"},
                "gender": {"type": "string", "enum": ["male", "female", "other"]},
                "birthday": {"type": "string", "description": "YYYY-MM-DD, if known"},
                "parent_name": {"type": "string", "description": "For a grandchild: which of Cordia's children is their parent"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "interests": {"type": "array", "items": {"type": "string"}},
                "personality_notes": {"type": "string"},
            },
            "required": ["name", "relationship"],
        },
    },
    {
        "name": "update_family_member_notes",
        "description": "Update a family member's interests or personality notes when Cordia shares new information about them. Use this to keep profiles current as you learn more.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Family member's name"},
                "field": {
                    "type": "string",
                    "enum": ["interests", "personality_notes", "nickname", "school_name", "grade_level"],
                    "description": "Which field to update",
                },
                "value": {"type": "string", "description": "New value or addition to append"},
            },
            "required": ["name", "field", "value"],
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
        "nickname": member.nickname,
        "relationship": member.relationship,
        "gender": member.gender,
        "city": member.city,
        "state": member.state,
        "birthday": member.birthday.isoformat() if member.birthday else None,
        # Contact details are deliberately withheld — the system prompt forbids
        # ever reading them back, and the send paths resolve addresses
        # server-side. Presence flags only, matching find_contact.
        "has_email": bool(member.email),
        "has_phone": bool(member.phone),
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
                "nickname": m.nickname,
                "relationship": m.relationship,
                "gender": m.gender,
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


async def get_grandkid_activity_balance_handler(db: AsyncSession, **kwargs) -> dict:
    return await family_service.get_grandkid_activity_balance(db)


async def log_grandkid_activity_handler(
    db: AsyncSession,
    title: str,
    activity_date: str,
    category: str,
    participant_names: list[str],
    notes: str | None = None,
    **kwargs,
) -> dict:
    from datetime import date
    parsed_date = date.fromisoformat(activity_date)
    activity = await family_service.log_grandkid_activity(
        db, title=title, activity_date=parsed_date,
        category=category, participant_names=participant_names, notes=notes,
    )
    return {
        "logged": True,
        "activity_id": str(activity.id),
        "title": title,
        "participants": participant_names,
    }


async def update_family_member_notes_handler(db: AsyncSession, name: str, field: str, value: str, **kwargs) -> dict:
    success = await family_service.update_family_member_notes(db, name, field, value)
    return {"updated": success, "name": name, "field": field}


async def add_family_member_handler(db: AsyncSession, **kwargs) -> dict:
    """Create a new family member. Refuses if the name already exists so a
    typo can't silently fork someone into two profiles."""
    from datetime import date as _date

    name = (kwargs.get("name") or "").strip()
    if not name:
        return {"added": False, "message": "A name is required."}

    existing = await family_service.get_family_member_by_name(db, name)
    if existing is not None:
        return {
            "added": False,
            "existing_name": existing.name,
            "message": f"{existing.name} is already in the family profiles — use update_family_member_notes instead.",
        }

    parent_name = kwargs.pop("parent_name", None)
    birthday = kwargs.pop("birthday", None)
    if birthday:
        try:
            kwargs["birthday"] = _date.fromisoformat(birthday)
        except ValueError:
            return {"added": False, "message": f"Couldn't read the birthday '{birthday}' — use YYYY-MM-DD."}

    member = await family_service.create_family_member(db, **kwargs)

    if parent_name:
        parent = await family_service.get_family_member_by_name(db, parent_name)
        if parent is not None:
            member.parent_id = parent.id
            await db.commit()

    return {"added": True, "name": member.name, "relationship": member.relationship}
