"""Feature requests: Cordia can ask for new capabilities, and Cord records
them for the team to build. Stored as memories (category 'feature_request')
so they surface in the same recall the assistant already uses.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import memory_service

TOOL_SCHEMAS = [
    {
        "name": "request_feature",
        "description": (
            "Record something Cordia wishes you could do but can't yet, so the team can "
            "build it. Use whenever she asks for a capability you don't have, expresses a "
            "wish ('it would be great if you could...'), or when you offer to note an idea "
            "and she agrees. Confirm warmly in one line that you've logged it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short label (e.g. 'text me a weekly grandkid photo recap')"},
                "details": {"type": "string", "description": "What she wants, in her words, plus any context on why"},
                "priority": {
                    "type": "string",
                    "enum": ["nice_to_have", "would_use_often", "really_needs_this"],
                    "description": "How much she seems to want it, inferred from how she asked",
                },
            },
            "required": ["title", "details"],
        },
    },
    {
        "name": "list_feature_requests",
        "description": "Show the capabilities Cordia has asked for so far. Use when she asks what she's requested or what's coming.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


async def request_feature_handler(db: AsyncSession, **kw) -> dict:
    priority = kw.get("priority") or "nice_to_have"
    await memory_service.store_memory(
        db,
        category="feature_request",
        subject=kw["title"][:200],
        content=f"[{priority}] {kw['details']}",
        tags=["feature_request", priority],
        source="conversation",
    )
    return {
        "logged": True,
        "title": kw["title"],
        "message": "Tell Cordia warmly that you've written it down for the team — one short line, no promises about timing.",
    }


async def list_feature_requests_handler(db: AsyncSession, **kw) -> dict:
    items = await memory_service.get_memories_by_category(db, "feature_request")
    return {
        "count": len(items),
        "requests": [
            {"title": m.subject, "details": m.content, "asked_on": m.created_at.date().isoformat() if m.created_at else None}
            for m in items
        ],
    }


HANDLERS = {
    "request_feature": request_feature_handler,
    "list_feature_requests": list_feature_requests_handler,
}
