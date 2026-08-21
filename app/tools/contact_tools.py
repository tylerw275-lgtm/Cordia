"""Cord's address book (owner-only).

Privacy design: stored emails/phones are NEVER returned to the model — lookups
report only whether an address is on file. Outbound sending resolves the real
address server-side (see outbound_tools), so contact details can't leak into
conversation or message bodies.
"""
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.models.family import FamilyMember
from app.utils.phone import normalize_phone

TOOL_SCHEMAS = [
    {
        "name": "add_contact",
        "description": (
            "Save a new contact (or a newly learned email/phone) to Cord's address book so "
            "Cordia never has to repeat it. Use immediately whenever she shares someone's "
            "contact info. Details are stored securely and never shown back in conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name (e.g. 'Jordan Ellis')"},
                "email": {"type": "string", "description": "Email address, if given"},
                "phone": {"type": "string", "description": "Mobile number, if given"},
                "relationship": {"type": "string", "description": "e.g. 'daughter-in-law', 'Naples property manager'"},
                "notes": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Group labels, e.g. ['st-thomas-2026']"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "find_contact",
        "description": (
            "Look up a contact by name. Returns whether an email/phone is on file (never the "
            "actual values) plus relationship, tags, and notes. Family members' stored "
            "emails/phones are checked too."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_contacts",
        "description": "List contacts, optionally filtered by a tag (e.g. everyone tagged 'st-thomas-2026').",
        "input_schema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "description": "Filter by group tag (optional)"}},
        },
    },
    {
        "name": "invite_to_sms",
        "description": (
            "Start onboarding a new person into Cordia's SMS circle. Saves/updates the "
            "contact, marks them invited, and returns a ready-to-forward invitation "
            "message containing the consent form link. IMPORTANT: Cord can NEVER text a "
            "new number first (carrier rule — even asking for consent counts). Give "
            "Cordia the invitation to forward from HER phone, or offer to email it via "
            "create_outbound_drafts if an email is on file. Once they sign the form, "
            "Cord can text them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Who Cordia wants to engage by text"},
                "phone": {"type": "string", "description": "Their mobile number, if she gave it"},
                "email": {"type": "string", "description": "Their email, if she gave it (enables emailing the invite)"},
                "relationship": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_sms_roster",
        "description": (
            "Cordia's growing SMS circle: who has consented and can be texted, who's been "
            "invited but hasn't signed the consent form yet (worth a nudge from her), who "
            "opted out, and who has a phone on file but no invite yet. Names only — never "
            "numbers."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_contact",
        "description": (
            "Update an existing contact — new email/phone, add tags (e.g. tag the St. Thomas "
            "trip guests), notes, or mark them trusted for inbound email capture (only when "
            "Cordia asks for that)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contact to update"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "add_tags": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
                "trusted_inbound": {"type": "boolean", "description": "Allow Cord to process this person's inbound emails (Cordia's call only)"},
            },
            "required": ["name"],
        },
    },
]


def _normalize_phone_e164(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    return f"+1{digits[-10:]}" if len(digits) <= 11 else f"+{digits}"


async def get_contact_by_name(db: AsyncSession, name: str) -> Contact | None:
    from app.services.family_service import like_escape
    q = f"%{like_escape((name or '').strip().lower())}%"
    result = await db.execute(select(Contact).where(func.lower(Contact.name).like(q, escape="\\")))
    return result.scalars().first()


async def resolve_recipient(db: AsyncSession, name: str) -> dict:
    """Server-side address resolution for outbound sending. Checks the contact
    book first, then family member profiles. Returns real addresses — for
    internal use only, never placed in a tool result."""
    contact = await get_contact_by_name(db, name)
    if contact and (contact.email or contact.phone):
        return {"name": contact.name, "email": contact.email, "phone": contact.phone}
    from app.services.family_service import get_family_member_by_name
    member = await get_family_member_by_name(db, name)
    if member:
        return {"name": member.name, "email": member.email, "phone": member.phone}
    return {"name": name, "email": None, "phone": None}


def _public_view(c: Contact) -> dict:
    return {
        "name": c.name,
        "relationship": c.relationship,
        "has_email": bool(c.email),
        "has_phone": bool(c.phone),
        "tags": c.tags or [],
        "notes": c.notes,
        "trusted_inbound": c.trusted_inbound,
    }


async def add_contact_handler(db: AsyncSession, **kw) -> dict:
    existing = await get_contact_by_name(db, kw["name"])
    if existing:
        # Treat as an update — never create duplicates for the same person
        return await update_contact_handler(db, name=kw["name"], email=kw.get("email"),
                                            phone=kw.get("phone"), notes=kw.get("notes"),
                                            add_tags=kw.get("tags"))
    contact = Contact(
        name=kw["name"].strip(),
        email=(kw.get("email") or "").strip().lower() or None,
        phone=_normalize_phone_e164(kw.get("phone")),
        relationship=kw.get("relationship"),
        notes=kw.get("notes"),
        tags=kw.get("tags"),
    )
    db.add(contact)
    await db.commit()
    return {"saved": True, "name": contact.name, "has_email": bool(contact.email), "has_phone": bool(contact.phone),
            "message": "Saved securely. I won't need to ask for this again."}


async def find_contact_handler(db: AsyncSession, **kw) -> dict:
    contact = await get_contact_by_name(db, kw["name"])
    if contact:
        return {"found": True, **_public_view(contact)}
    from app.services.family_service import get_family_member_by_name
    member = await get_family_member_by_name(db, kw["name"])
    if member:
        return {"found": True, "name": member.name, "relationship": member.relationship,
                "has_email": bool(member.email), "has_phone": bool(member.phone),
                "source": "family_profile"}
    return {"found": False, "message": f"No contact on file for {kw['name']} — ask Cordia for their details and save with add_contact."}


async def list_contacts_handler(db: AsyncSession, **kw) -> dict:
    stmt = select(Contact).order_by(Contact.name)
    if kw.get("tag"):
        stmt = stmt.where(Contact.tags.contains([kw["tag"]]))
    result = await db.execute(stmt)
    contacts = result.scalars().all()
    return {"count": len(contacts), "contacts": [_public_view(c) for c in contacts]}


async def update_contact_handler(db: AsyncSession, **kw) -> dict:
    contact = await get_contact_by_name(db, kw["name"])
    if not contact:
        # Auto-create so "update" on a new name still lands somewhere useful
        return await add_contact_handler(db, name=kw["name"], email=kw.get("email"),
                                         phone=kw.get("phone"), notes=kw.get("notes"),
                                         tags=kw.get("add_tags"))
    if kw.get("email"):
        contact.email = kw["email"].strip().lower()
    if kw.get("phone"):
        contact.phone = _normalize_phone_e164(kw["phone"])
    if kw.get("notes"):
        contact.notes = f"{contact.notes}\n{kw['notes']}" if contact.notes else kw["notes"]
    if kw.get("add_tags"):
        contact.tags = sorted(set(contact.tags or []) | set(kw["add_tags"]))
    if kw.get("trusted_inbound") is not None:
        contact.trusted_inbound = bool(kw["trusted_inbound"])
    await db.commit()
    return {"updated": True, **_public_view(contact)}


def _format_program_number(e164: str) -> str:
    digits = re.sub(r"\D", "", e164 or "")[-10:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return e164 or "the Cord number"


def build_invite_message(first_name: str, consent_url: str, program_number: str) -> str:
    """The invitation Cordia forwards from her own phone (or Cord emails with
    her approval). Person-to-person from her = fully compliant; the recipient
    opts in themselves via the form."""
    return (
        f"Hi {first_name}! I've set up a personal assistant called Cord that helps me "
        f"stay on top of family plans and trips. If you'd like it to be able to text "
        f"with you too, sign this quick consent form (takes under a minute): "
        f"{consent_url} — then text START to {program_number}. "
        f"You can opt out anytime by replying STOP."
    )


async def invite_to_sms_handler(db: AsyncSession, **kw) -> dict:
    contact = await get_contact_by_name(db, kw["name"])
    if not contact:
        await add_contact_handler(db, name=kw["name"], phone=kw.get("phone"),
                                  email=kw.get("email"), relationship=kw.get("relationship"))
        contact = await get_contact_by_name(db, kw["name"])
    else:
        if kw.get("phone"):
            contact.phone = _normalize_phone_e164(kw["phone"])
        if kw.get("email"):
            contact.email = kw["email"].strip().lower()
    contact.sms_invited_at = datetime.now(timezone.utc)
    await db.commit()

    first_name = contact.name.split()[0]
    invite = build_invite_message(
        first_name,
        f"{settings.public_base_url.rstrip('/')}/consent",
        _format_program_number(settings.twilio_phone_number),
    )
    return {
        "invited": True,
        "name": contact.name,
        "has_email": bool(contact.email),
        "forward_this": invite,
        "message": (
            "Give Cordia this message to forward from her own phone (or offer to email it "
            "for her approval if they have an email on file). Cord cannot text them until "
            "they sign the form — once they do, they join her SMS circle automatically."
        ),
    }


async def _consent_by_phone(db: AsyncSession) -> dict[str, str]:
    """Map of normalized phone -> 'consented' | 'opted_out'."""
    result = await db.execute(text("SELECT phone, consented_at, opted_out_at FROM sms_consent"))
    status: dict[str, str] = {}
    for phone, consented_at, opted_out_at in result.fetchall():
        key = normalize_phone(phone)
        if not key:
            continue
        if opted_out_at is not None:
            status[key] = "opted_out"
        elif consented_at is not None:
            status[key] = "consented"
    return status


async def list_sms_roster_handler(db: AsyncSession, **kw) -> dict:
    consent = await _consent_by_phone(db)
    roster = {"consented": [], "invited_pending": [], "opted_out": [], "not_invited": []}
    seen: set[str] = set()

    contacts = (await db.execute(select(Contact).order_by(Contact.name))).scalars().all()
    for c in contacts:
        key = normalize_phone(c.phone)
        state = consent.get(key) if key else None
        seen.add(c.name.strip().lower())
        if state == "consented":
            roster["consented"].append(c.name)
        elif state == "opted_out":
            roster["opted_out"].append(c.name)
        elif c.sms_invited_at is not None:
            roster["invited_pending"].append(c.name)
        elif key:
            roster["not_invited"].append(c.name)

    members = (await db.execute(select(FamilyMember).where(FamilyMember.phone.isnot(None)))).scalars().all()
    for m in members:
        if m.name.strip().lower() in seen:
            continue
        state = consent.get(normalize_phone(m.phone))
        if state == "consented":
            roster["consented"].append(m.name)
        elif state == "opted_out":
            roster["opted_out"].append(m.name)
        else:
            roster["not_invited"].append(m.name)

    return {
        **roster,
        "counts": {k: len(v) for k, v in roster.items()},
        "hint": (
            "invited_pending haven't signed the consent form yet — suggest Cordia nudge them "
            "personally. not_invited have a phone on file but no invite; offer invite_to_sms."
        ),
    }


HANDLERS = {
    "add_contact": add_contact_handler,
    "find_contact": find_contact_handler,
    "list_contacts": list_contacts_handler,
    "update_contact": update_contact_handler,
    "invite_to_sms": invite_to_sms_handler,
    "list_sms_roster": list_sms_roster_handler,
}
