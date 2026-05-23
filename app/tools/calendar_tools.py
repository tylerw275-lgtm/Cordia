from sqlalchemy.ext.asyncio import AsyncSession

from app.services import family_service

TOOL_SCHEMAS = [
    {
        "name": "schedule_family_event",
        "description": "Schedule a family gathering or event, taking into account school calendars and existing commitments.",
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
                "recurrence": {
                    "type": "string",
                    "enum": ["annual", "one_time"],
                    "description": "Whether this event repeats annually",
                },
            },
            "required": ["title", "event_type", "event_date"],
        },
    },
]


async def schedule_event_handler(db: AsyncSession, **kwargs) -> dict:
    from datetime import date
    event_date = date.fromisoformat(kwargs.pop("event_date"))
    event = await family_service.create_family_event(db, event_date=event_date, **kwargs)
    return {
        "scheduled": True,
        "event_id": str(event.id),
        "title": event.title,
        "date": event.event_date.isoformat(),
    }
