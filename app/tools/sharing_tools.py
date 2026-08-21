"""Cordia decides what crosses between the people who use Cord.

The design turns on one distinction. A **grant** is standing permission — Karie
can see the loyalty accounts until Cordia says otherwise. A **briefing** is a
single message sent once, on Cordia's instruction, that grants nothing. Telling
Tom about the Naples project must not subscribe him to it, and the reason those
are separate tools is so the model cannot blur them.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import principal_service

logger = logging.getLogger(__name__)


TOOL_SCHEMAS = [
    {
        "name": "list_people_with_access",
        "description": (
            "Show who else can use Cord and what Cordia has shared with each of them. "
            "Use when she asks who has access, what Karie can see, or before sharing "
            "something so she knows the current state."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "share_with",
        "description": (
            "Give someone standing access to an area of Cordia's information — her loyalty "
            "accounts, travel preferences, leases, family notes, memories, or her projects. "
            "ONLY call this when Cordia has clearly said to share that specific thing with "
            "that specific person. Never infer it, and never share on her behalf. This is "
            "permanent until revoked, unlike a one-off briefing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string", "description": "Who to share with, by name"},
                "what": {
                    "type": "string",
                    "description": ("Area to share: loyalty, travel_prefs, leases, family_notes, "
                                    "memories, or projects"),
                },
                "project_id": {
                    "type": "string",
                    "description": "To share ONE project rather than all of them, pass its id with what='projects'",
                },
            },
            "required": ["person", "what"],
        },
    },
    {
        "name": "stop_sharing",
        "description": (
            "Take away access Cordia previously gave. Use when she says to stop sharing, "
            "revoke, or cut someone off from something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string"},
                "what": {"type": "string", "description": "The area to revoke"},
                "project_id": {"type": "string", "description": "To revoke just one project"},
            },
            "required": ["person", "what"],
        },
    },
    {
        "name": "brief_person",
        "description": (
            "Send someone a one-off update because Cordia asked you to — 'let Tom know we're "
            "leaving Friday', 'fill Karie in on the Naples trip'. This sends ONCE and grants "
            "no ongoing access; they will not be updated again unless Cordia says so. Use "
            "share_with instead if she wants them to be able to look it up themselves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string"},
                "message": {
                    "type": "string",
                    "description": "What to tell them, written warmly in your own voice on her behalf",
                },
            },
            "required": ["person", "message"],
        },
    },
]


async def _find(db: AsyncSession, name: str):
    return await principal_service.find_by_name(db, name)


def _unknown(name: str) -> dict:
    return {
        "ok": False, "reason": "unknown_person",
        "message": (f"Nobody using Cord is called {name}. Only people Cordia has set up can be "
                    "shared with — this is not the family roster."),
    }


async def list_people_with_access_handler(db: AsyncSession, **kw) -> dict:
    people = await principal_service.list_principals(db)
    out = []
    for person in people:
        shared = []
        if not person.is_owner:
            for scope in principal_service.SHAREABLE_SCOPES:
                if await principal_service.has_access(db, person, scope):
                    shared.append(scope)
        out.append({
            "name": person.name,
            "role": "account holder" if person.is_owner else "has their own assistant",
            "reaches_you_by": ", ".join(
                x for x in (("text" if person.phone else ""), ("email" if person.email else "")) if x
            ) or "no contact details on file",
            "can_see_of_cordias": shared or ["nothing"],
            "active": person.is_active,
        })
    return {
        "people": out,
        "note": (
            "Everyone listed has their own separate workspace. They see nothing of Cordia's "
            "except what is listed under can_see_of_cordias. Do not describe one person's work "
            "to another."
        ),
    }


async def share_with_handler(db: AsyncSession, **kw) -> dict:
    person = await _find(db, kw.get("person", ""))
    if person is None:
        return _unknown(kw.get("person", ""))
    if person.is_owner:
        return {"ok": False, "reason": "is_owner",
                "message": f"{person.name} is the account holder — it is all hers already."}

    what = (kw.get("what") or "").strip().lower()
    if what not in principal_service.SHAREABLE_SCOPES:
        return {"ok": False, "reason": "unknown_area",
                "message": (f"'{what}' is not something that can be shared. The areas are: "
                            f"{', '.join(principal_service.SHAREABLE_SCOPES)}. Ask Cordia which "
                            "she means rather than guessing.")}

    owner = await principal_service.get_owner(db)
    project_id = kw.get("project_id") or None
    created = await principal_service.grant(db, person, what, project_id, granted_by=owner)
    target = f"one project ({project_id})" if project_id else f"her {what.replace('_', ' ')}"
    return {
        "ok": True,
        "already_had_it": not created,
        "message": (
            f"{person.name} can now see {target}, and can look it up whenever they like until "
            f"Cordia revokes it. Confirm that to her in one line."
            if created else
            f"{person.name} already had access to {target}. Nothing changed."
        ),
    }


async def stop_sharing_handler(db: AsyncSession, **kw) -> dict:
    person = await _find(db, kw.get("person", ""))
    if person is None:
        return _unknown(kw.get("person", ""))
    what = (kw.get("what") or "").strip().lower()
    closed = await principal_service.revoke(db, person, what, kw.get("project_id") or None)
    return {
        "ok": True, "revoked": closed,
        "message": (
            f"{person.name} can no longer see Cordia's {what.replace('_', ' ')}."
            if closed else
            f"{person.name} did not have access to {what.replace('_', ' ')}, so nothing changed."
        ),
    }


async def brief_person_handler(db: AsyncSession, **kw) -> dict:
    person = await _find(db, kw.get("person", ""))
    if person is None:
        return _unknown(kw.get("person", ""))

    message = (kw.get("message") or "").strip()
    if not message:
        return {"sent": False, "reason": "empty", "message": "Nothing to send."}

    from app.services import consent_service, email_service, sms_service

    # Text if they have consented and been approved; otherwise email. Cord never
    # texts anyone first, and a principal is no exception — being set up is not
    # the same as having opted in to SMS.
    if person.phone and await consent_service.is_approved(db, person.phone):
        try:
            await sms_service.send_sms(to=person.phone, body=message)
            channel = "text"
        except Exception as e:
            logger.error(f"Could not brief {person.name} by text: {e}")
            return {"sent": False, "reason": "send_failed",
                    "message": f"The text to {person.name} did not go through. Tell Cordia plainly."}
    elif person.email:
        result = await email_service.send_email(
            to=person.email, subject="A note from Cordia", body_markdown=message
        )
        if not result.get("sent"):
            return {"sent": False, "reason": "send_failed",
                    "message": f"The email to {person.name} did not go through. Tell Cordia plainly."}
        channel = "email"
    else:
        return {
            "sent": False, "reason": "no_route",
            "message": (
                f"{person.name} has no email on file and has not consented to texts, so there "
                "is no way to reach them. Ask Cordia for an email address, or give her the "
                "message to forward herself."
            ),
        }

    return {
        "sent": True, "person": person.name, "channel": channel,
        "message": (
            f"Sent to {person.name} by {channel}. This was a one-off — they have NOT been given "
            "ongoing access and will not hear about this again unless Cordia says so. If she "
            "wants them able to look it up themselves, that is share_with."
        ),
    }


HANDLERS = {
    "list_people_with_access": list_people_with_access_handler,
    "share_with": share_with_handler,
    "stop_sharing": stop_sharing_handler,
    "brief_person": brief_person_handler,
}
