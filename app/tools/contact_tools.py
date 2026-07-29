"""Cord's address book (owner-only).

Privacy design: stored emails/phones are NEVER returned to the model — lookups
report only whether an address is on file. Outbound sending resolves the real
address server-side (see outbound_tools), so contact details can't leak into
conversation or message bodies.
"""
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.family import FamilyMember

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
                "name": {"type": "string", "description": "Full name (e.g. 'Kristen Wilkinson')"},
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
    q = f"%{(name or '').strip().lower()}%"
    result = await db.execute(select(Contact).where(func.lower(Contact.name).like(q)))
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


HANDLERS = {
    "add_contact": add_contact_handler,
    "find_contact": find_contact_handler,
    "list_contacts": list_contacts_handler,
    "update_contact": update_contact_handler,
}
