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
                "about_name": {"type": "string", "description": "Who the gift is for (e.g. one of the grandchildren). Omit if it's for the person texting."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "share_engagement_tip",
        "description": "Record a helpful tip about how this person likes Cordia to connect with them or their family (e.g. 'I love a phone call over text', 'he lights up over one-on-one time').",
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
                "name": {"type": "string", "description": "Family member to update, by name"},
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
            "properties": {"name": {"type": "string", "description": "Family member's name"}},
            "required": ["name"],
        },
    },
    {
        "name": "request_family_input",
        "description": (
            "Queue a question for the family — e.g. source gift ideas for a grandchild, or "
            "send calendar dates. IMPORTANT: this is PASSIVE. It does not message anyone; the "
            "question is shown to them the next time THEY text in, which may be never. Tell "
            "Cordia that plainly, and if the person has consented to texts, offer to send it "
            "to them now instead via create_outbound_drafts (which she then approves)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to ask the family for (e.g. 'gift ideas for a grandchild's birthday')"},
                "about_name": {"type": "string", "description": "Who it concerns, if applicable"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "ask_family_member",
        "description": (
            "Text a question directly to ONE family member who has joined the circle and "
            "consented to messages — e.g. 'ask Kristen what Elijah is into right now'. This "
            "actually sends; use it when Cordia wants an answer rather than a queued note. "
            "Write the question warmly, in your own voice, on her behalf. Their reply comes "
            "back to you and is passed to her. If the person hasn't consented, this reports "
            "that instead of sending — then offer to email them or give Cordia a message to "
            "forward herself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Which family member to ask"},
                "question": {"type": "string", "description": "The full message to send, written warmly and clearly on Cordia's behalf"},
                "about_name": {"type": "string", "description": "Who the question concerns, if applicable"},
            },
            "required": ["name", "question"],
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
    return {
        "queued": True,
        "prompt": req.prompt,
        "delivery": "passive_only",
        "message": (
            "Say plainly that this is queued for the next time they text in, not sent. "
            "Then offer to send it directly: check list_sms_roster for who has consented "
            "and, for those who have, draft a note with create_outbound_drafts for her "
            "approval. If outbound sending isn't available, say so rather than implying "
            "the question went out."
        ),
    }


async def ask_family_member_handler(db: AsyncSession, **kw) -> dict:
    """Send Cordia's question to a consented circle member, for real.

    Guarded three ways: the person must already be in the family circle, must
    have a live consent record, and the message is only ever the question Cord
    composed — never stored contact data.
    """
    from sqlalchemy import text as sql_text
    from app.services import sms_service
    from app.utils.phone import normalize_phone

    member = await family_service.get_family_member_by_name(db, kw["name"])
    if not member:
        return {"sent": False, "reason": "unknown_person",
                "message": f"No family member on file named {kw['name']}."}
    if not member.has_circle_access:
        return {"sent": False, "reason": "not_in_circle",
                "message": (f"{member.name} hasn't been given circle access yet. Offer to set "
                            "that up with grant_family_circle_access first.")}
    if not member.phone:
        return {"sent": False, "reason": "no_number",
                "message": f"No mobile number on file for {member.name} — ask Cordia for it."}

    digits = normalize_phone(member.phone)
    result = await db.execute(
        sql_text(
            "SELECT 1 FROM sms_consent "
            "WHERE right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) = :digits "
            "AND consented_at IS NOT NULL AND opted_out_at IS NULL"
        ),
        {"digits": digits},
    )
    if result.first() is None:
        return {"sent": False, "reason": "no_consent",
                "message": (f"{member.name} hasn't consented to texts, so nothing was sent. "
                            "Tell Cordia, and offer to email them or give her a note to "
                            "forward herself.")}

    about = None
    if kw.get("about_name"):
        about = await family_service.get_family_member_by_name(db, kw["about_name"])
    await circle.create_request(
        db, kw["question"], about_member_id=about.id if about else None, audience=str(member.id)
    )

    delivered = await sms_service.send_sms(to=member.phone, body=kw["question"])
    if not delivered:
        return {"sent": False, "reason": "suppressed",
                "message": f"The message to {member.name} was not delivered — tell Cordia."}
    return {
        "sent": True,
        "to": member.name,
        "message": (f"Sent to {member.name}. Tell Cordia it's on its way and that you'll pass "
                    "along the reply when it comes in."),
    }


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
    "ask_family_member": ask_family_member_handler,
    "get_family_circle_updates": get_family_circle_updates_handler,
    "share_event_with_family": share_event_with_family_handler,
}
