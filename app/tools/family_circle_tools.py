"""Tools for the family circle.

Two groups:
- FAMILY_TOOL_SCHEMAS / family handlers: used when an opted-in family member
  texts in. Deliberately restricted — no access to Cordia's private data.
- OWNER_TOOL_SCHEMAS / owner handlers: extra tools available to Cordia for
  managing the circle (grant access, ask family to source ideas, review what
  family has shared, approve what family may see).
"""
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyMember
from app.services import family_circle_service as circle
from app.services import family_service

# ---------------------------------------------------------------------------
# FAMILY-FACING TOOLS  (actor = the family member texting in)
# ---------------------------------------------------------------------------

FAMILY_TOOL_SCHEMAS = [
    {
        "name": "share_gift_idea",
        "description": "Record a gift idea this family member wants Cordia to know about — for themselves or for someone in their household. Surfaced to Cordia when she asks about gifts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The gift idea, in their words"},
                "about_name": {"type": "string", "description": "Who the gift is for (e.g. 'Brighton'). Omit if it's for the person texting."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "share_engagement_tip",
        "description": "Record a helpful tip about how this person likes Cordia to connect with them or their family (e.g. 'I love a phone call over text', 'Brighton lights up over one-on-one time').",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The tip, in their words"},
                "about_name": {"type": "string", "description": "Who the tip is about. Omit if about the person texting."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "update_relative_interests",
        "description": "Update the interests or notes for a member of this person's household (e.g. their child's current hobbies). Helps Cordia plan well.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Family member to update (e.g. 'Elijah')"},
                "interests": {"type": "string", "description": "Comma-separated interests to add"},
                "notes": {"type": "string", "description": "A personality/context note to add"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "submit_calendar_date",
        "description": "Add a date from this family's calendar (e.g. a school break, recital, trip) so Cordia can plan around it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "event_date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
            },
            "required": ["title", "event_date"],
        },
    },
    {
        "name": "request_conversation",
        "description": "Let Cordia know this person would value a meaningful one-on-one talk with her when she has time. Use when they express wanting real conversation or to discuss something important.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Optional brief context on what they'd like to talk about"},
            },
            "required": [],
        },
    },
    {
        "name": "view_shared_schedule",
        "description": "Show upcoming events on Cordia's calendar that she has approved to share with family.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


async def _resolve_about(db: AsyncSession, about_name: str | None, default: FamilyMember):
    if not about_name:
        return default
    m = await family_service.get_family_member_by_name(db, about_name)
    return m or default


async def share_gift_idea_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    about = await _resolve_about(db, kw.get("about_name"), acting_member)
    await circle.add_input(db, acting_member.id, "gift_idea", kw["content"], about_member_id=about.id)
    # Mark any open "source gift ideas" requests as fulfilled
    for req in await circle.get_open_requests_for(db, acting_member):
        await circle.fulfill_request(db, req.id)
    return {"recorded": True, "for": about.name, "message": "Cordia will see this when she's thinking about gifts."}


async def share_engagement_tip_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    about = await _resolve_about(db, kw.get("about_name"), acting_member)
    await circle.add_input(db, acting_member.id, "engagement_tip", kw["content"], about_member_id=about.id)
    return {"recorded": True, "about": about.name}


async def update_relative_interests_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    name = kw["name"]
    ok = False
    if kw.get("interests"):
        ok = await family_service.update_family_member_notes(db, name, "interests", kw["interests"]) or ok
    if kw.get("notes"):
        ok = await family_service.update_family_member_notes(db, name, "personality_notes", kw["notes"]) or ok
    return {"updated": ok, "name": name}


async def submit_calendar_date_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    event = await circle.submit_calendar_event(
        db, acting_member.id, kw["title"], date.fromisoformat(kw["event_date"]), kw.get("notes")
    )
    return {"added": True, "title": event.title, "date": event.event_date.isoformat()}


async def request_conversation_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    content = kw.get("content") or f"{acting_member.name} would love a meaningful talk with you when you have time."
    await circle.add_input(db, acting_member.id, "mac_request", content)
    return {"recorded": True, "message": "I'll let Cordia know you'd love to connect."}


async def view_shared_schedule_handler(db: AsyncSession, acting_member: FamilyMember, **kw) -> dict:
    events = await circle.get_shared_schedule(db)
    return {
        "events": [{"title": e.title, "date": e.event_date.isoformat()} for e in events],
        "count": len(events),
        "note": "Only events Cordia has chosen to share are shown." if not events else None,
    }


FAMILY_HANDLERS = {
    "share_gift_idea": share_gift_idea_handler,
    "share_engagement_tip": share_engagement_tip_handler,
    "update_relative_interests": update_relative_interests_handler,
    "submit_calendar_date": submit_calendar_date_handler,
    "request_conversation": request_conversation_handler,
    "view_shared_schedule": view_shared_schedule_handler,
}


# ---------------------------------------------------------------------------
# OWNER-FACING TOOLS  (actor = Cordia)
# ---------------------------------------------------------------------------

OWNER_TOOL_SCHEMAS = [
    {
        "name": "grant_family_circle_access",
        "description": "Give a family member access to contribute to the family circle. After this, they text the assistant's number once to opt in. Use when Cordia wants someone to be able to share ideas, calendars, or tips.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Family member's name (e.g. 'Aaron')"}},
            "required": ["name"],
        },
    },
    {
        "name": "request_family_input",
        "description": "Ask the family to contribute something — e.g. source gift ideas for a grandchild, or send their calendar dates. The relevant family members are prompted the next time they text in, and their answers come back to Cordia. Use when Cordia says yes to 'would you like me to ask them?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to ask the family for (e.g. 'gift ideas for Bea's birthday')"},
                "about_name": {"type": "string", "description": "Who it concerns, if applicable (e.g. 'Bea')"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "get_family_circle_updates",
        "description": "Retrieve everything family members have shared but Cordia hasn't seen yet — gift ideas, tips, and requests to talk. Use when she asks what's new from the family, or to weave in updates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "share_event_with_family",
        "description": "Approve an upcoming event on Cordia's calendar so family members can see it when they ask. Nothing on her calendar is visible to family until she approves it this way.",
        "input_schema": {
            "type": "object",
            "properties": {"event_title": {"type": "string", "description": "Title (or part) of the event to share"}},
            "required": ["event_title"],
        },
    },
]


async def grant_family_circle_access_handler(db: AsyncSession, **kw) -> dict:
    from app.config import settings
    member = await circle.grant_circle_access(db, kw["name"])
    if not member:
        return {"granted": False, "message": f"No family member found named {kw['name']}."}
    consent_url = f"{settings.public_base_url}/consent"
    return {
        "granted": True,
        "name": member.name,
        "consent_form_url": consent_url,
        "message": (
            f"{member.name} can now contribute. Tell Cordia to share this consent form link "
            f"with them directly ({consent_url}) — once they sign it electronically, they text "
            "this number to get started. The assistant cannot text them first; they must sign "
            "and reach out themselves."
        ),
    }


async def request_family_input_handler(db: AsyncSession, **kw) -> dict:
    about = None
    if kw.get("about_name"):
        about = await family_service.get_family_member_by_name(db, kw["about_name"])
    req = await circle.create_request(db, kw["prompt"], about_member_id=about.id if about else None)
    return {"requested": True, "prompt": req.prompt, "message": "I'll gather this from the family and report back."}


async def get_family_circle_updates_handler(db: AsyncSession, **kw) -> dict:
    inputs = await circle.get_unsurfaced_inputs(db)
    if not inputs:
        return {"updates": [], "count": 0}
    out = []
    ids = []
    for item in inputs:
        from_m = await family_service.get_family_member(db, item.from_member_id)
        about_m = await family_service.get_family_member(db, item.about_member_id) if item.about_member_id else None
        out.append({
            "kind": item.kind,
            "from": from_m.name if from_m else "family",
            "about": about_m.name if about_m else None,
            "content": item.content,
        })
        ids.append(item.id)
    await circle.mark_inputs_surfaced(db, ids)
    return {"updates": out, "count": len(out)}


async def share_event_with_family_handler(db: AsyncSession, **kw) -> dict:
    from sqlalchemy import select, func
    from app.models.family import FamilyEvent
    from app.services.family_service import like_escape
    q = f"%{like_escape(kw['event_title'].lower())}%"
    result = await db.execute(
        select(FamilyEvent).where(func.lower(FamilyEvent.title).like(q, escape="\\"))
        .where(FamilyEvent.event_date >= date.today())
    )
    events = result.scalars().all()
    for e in events:
        e.shareable_with_family = True
    await db.commit()
    return {"shared": len(events), "titles": [e.title for e in events]}


OWNER_EXTRA_HANDLERS = {
    "grant_family_circle_access": grant_family_circle_access_handler,
    "request_family_input": request_family_input_handler,
    "get_family_circle_updates": get_family_circle_updates_handler,
    "share_event_with_family": share_event_with_family_handler,
}
