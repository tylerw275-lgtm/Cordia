"""Lease review tools.

The review flow starts from a photo or pasted text: Cord reads the document,
saves it with save_lease (which returns the id everything else needs), records
the risky clauses with flag_lease_clauses, and offers a renewal reminder.
"""
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

TOOL_SCHEMAS = [
    {
        "name": "save_lease",
        "description": (
            "Save a lease Cordia sent you (photo, PDF text, or pasted text) so it can be "
            "reviewed, stored and reminded on — normally a lease for space in a building "
            "she OWNS, with a tenant renting from her. Call this FIRST when she shares a lease — "
            "flag_lease_clauses and the renewal reminder both need the id it returns. Fill in "
            "whatever the document shows; ask her only for a date you genuinely cannot find."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_address": {"type": "string", "description": "The leased property's address"},
                "lease_start": {"type": "string", "description": "YYYY-MM-DD"},
                "lease_end": {"type": "string", "description": "YYYY-MM-DD"},
                "her_role": {
                    "type": "string",
                    "enum": ["landlord", "tenant"],
                    "description": "Which side Cordia is on. She OWNS commercial property and leases space to tenants, so this is almost always 'landlord'. Only use 'tenant' if she is the one renting space.",
                },
                "tenant_name": {"type": "string", "description": "Who is renting the space from her"},
                "landlord_name": {"type": "string", "description": "The owner — usually Cordia or one of her entities"},
                "monthly_rent": {"type": "number", "description": "Monthly rent amount"},
                "renewal_notice_days": {"type": "integer", "description": "Days of notice required before renewal/termination (default 60)"},
                "raw_text": {"type": "string", "description": "The lease text you read, for the record"},
                "set_renewal_reminder": {"type": "boolean", "description": "Default true — texts Cordia before the notice deadline"},
            },
            "required": ["property_address", "lease_start", "lease_end"],
        },
    },
    {
        "name": "list_leases",
        "description": (
            "List the leases on file: which tenant occupies which property, the rent they pay, "
            "when each lease expires and how long until she must act. Use when Cordia asks "
            "about her buildings, tenants, rent roll, or what's coming up — and before saving "
            "a lease that may already be stored."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_lease_details",
        "description": "Full detail on one lease: summary, every flagged clause with its severity, and reminders set. Use when she asks about a specific property.",
        "input_schema": {
            "type": "object",
            "properties": {"property_address": {"type": "string", "description": "Address (or part of it) to look up"}},
            "required": ["property_address"],
        },
    },
    {
        "name": "schedule_lease_reminder",
        "description": "Set a reminder about a lease — a notice deadline, a rent review, an inspection. Cord texts Cordia at that time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_address": {"type": "string"},
                "remind_on": {"type": "string", "description": "YYYY-MM-DD"},
                "message": {"type": "string", "description": "What to tell her when it fires"},
            },
            "required": ["property_address", "remind_on", "message"],
        },
    },
    {
        "name": "flag_lease_clauses",
        "description": "Analyze and flag key clauses in a lease document. Use when reviewing lease text. Stores findings to the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lease_id": {
                    "type": "string",
                    "description": "UUID of the lease record in the database",
                },
                "clauses": {
                    "type": "array",
                    "description": "List of clauses found in the lease",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clause_type": {
                                "type": "string",
                                "description": "Type of clause (e.g. renewal, termination, liability, rent_escalation, personal_guarantee)",
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["standard", "flag", "urgent"],
                                "description": "How important this clause is for Cordia to know about",
                            },
                            "content": {
                                "type": "string",
                                "description": "Exact or paraphrased text of the clause",
                            },
                            "note": {
                                "type": "string",
                                "description": "Plain-English explanation of what this means and why it matters",
                            },
                        },
                        "required": ["clause_type", "severity", "content", "note"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "1-2 sentence executive summary of the lease for Cordia",
                },
            },
            "required": ["lease_id", "clauses", "summary"],
        },
    },
]


async def flag_clauses_handler(db: AsyncSession, lease_id: str, clauses: list[dict], summary: str, **kwargs) -> dict:
    from app.models.real_estate import Lease, LeaseClause
    from sqlalchemy import select

    result = await db.execute(select(Lease).where(Lease.id == uuid.UUID(lease_id)))
    lease = result.scalar_one_or_none()
    if not lease:
        return {"success": False, "message": f"Lease {lease_id} not found"}

    lease.summary = summary
    stored_clauses = []
    for c in clauses:
        clause = LeaseClause(
            lease_id=lease.id,
            clause_type=c["clause_type"],
            severity=c["severity"],
            content=c["content"],
            claude_note=c["note"],
        )
        db.add(clause)
        stored_clauses.append({"type": c["clause_type"], "severity": c["severity"]})

    await db.commit()
    urgent = [c for c in clauses if c["severity"] == "urgent"]
    flagged = [c for c in clauses if c["severity"] == "flag"]
    return {
        "success": True,
        "lease_id": lease_id,
        "clauses_stored": len(clauses),
        "urgent_count": len(urgent),
        "flagged_count": len(flagged),
        "summary": summary,
    }


def _reminder_text(lease, notice_days: int, her_role: str) -> str:
    """Owner and tenant need to hear different things at the same deadline."""
    if her_role == "tenant":
        return (
            f"Heads up: your lease at {lease.property_address} ends "
            f"{lease.lease_end.isoformat()} and notice is due {notice_days} days before. "
            "Want to review renewal options?"
        )
    who = lease.tenant_name or "your tenant"
    return (
        f"Heads up: {who}'s lease at {lease.property_address} expires "
        f"{lease.lease_end.isoformat()} — {notice_days} days out from the notice date. "
        "Want to plan the renewal terms, or start lining up a replacement tenant?"
    )


async def _find_lease(db: AsyncSession, address: str):
    from sqlalchemy import func
    from app.models.real_estate import Lease
    q = f"%{(address or '').strip().lower()}%"
    result = await db.execute(
        select(Lease).where(func.lower(Lease.property_address).like(q)).order_by(Lease.created_at.desc())
    )
    return result.scalars().first()


async def save_lease_handler(db: AsyncSession, **kw) -> dict:
    from app.models.real_estate import Lease, LeaseReminder

    existing = await _find_lease(db, kw["property_address"])
    if existing:
        return {
            "saved": False,
            "already_on_file": True,
            "lease_id": str(existing.id),
            "message": (
                "A lease for that address is already stored. Use this lease_id to add clause "
                "findings, or tell Cordia it's already on file."
            ),
        }

    notice_days = int(kw.get("renewal_notice_days") or 60)
    lease = Lease(
        property_address=kw["property_address"],
        lease_start=date.fromisoformat(kw["lease_start"]),
        lease_end=date.fromisoformat(kw["lease_end"]),
        tenant_name=kw.get("tenant_name"),
        status="active",
        landlord_name=kw.get("landlord_name"),
        monthly_rent=kw.get("monthly_rent"),
        renewal_notice_days=notice_days,
        raw_text=(kw.get("raw_text") or "")[:20000] or None,
    )
    db.add(lease)
    await db.flush()

    reminder_set = False
    if kw.get("set_renewal_reminder", True):
        remind_at = datetime.combine(lease.lease_end, datetime.min.time()) - timedelta(days=notice_days)
        # A deadline already past is not worth a reminder; say so instead.
        if remind_at.date() > date.today():
            db.add(LeaseReminder(
                lease_id=lease.id,
                remind_at=remind_at,
                message=_reminder_text(lease, notice_days, kw.get("her_role", "landlord")),
            ))
            reminder_set = True

    await db.commit()
    await db.refresh(lease)
    return {
        "saved": True,
        "lease_id": str(lease.id),
        "property_address": lease.property_address,
        "lease_end": lease.lease_end.isoformat(),
        "renewal_reminder_set": reminder_set,
        "notice_deadline": (lease.lease_end - timedelta(days=notice_days)).isoformat(),
        "message": (
            "Now call flag_lease_clauses with this lease_id to record what you found, then "
            "give Cordia the plain-English summary and recommend her attorney review anything "
            "significant."
            if reminder_set else
            "Saved, but the notice deadline has already passed — tell Cordia that plainly."
        ),
    }


async def list_leases_handler(db: AsyncSession, **kw) -> dict:
    from app.models.real_estate import Lease
    result = await db.execute(select(Lease).order_by(Lease.lease_end))
    leases = result.scalars().all()
    today = date.today()
    out = []
    for l in leases:
        deadline = l.lease_end - timedelta(days=l.renewal_notice_days or 60)
        out.append({
            "property_address": l.property_address,
            "tenant": l.tenant_name,
            "lease_end": l.lease_end.isoformat(),
            "monthly_rent": float(l.monthly_rent) if l.monthly_rent else None,
            "status": l.status,
            "notice_deadline": deadline.isoformat(),
            "days_until_notice_deadline": (deadline - today).days,
            "summary": l.summary,
        })
    return {
        "count": len(out),
        "leases": out,
        "message": "No leases on file yet — she can text you a photo of one." if not out else None,
    }


async def get_lease_details_handler(db: AsyncSession, **kw) -> dict:
    from app.models.real_estate import LeaseClause, LeaseReminder
    lease = await _find_lease(db, kw["property_address"])
    if not lease:
        return {"found": False, "message": f"No lease on file matching '{kw['property_address']}'."}

    clauses = (await db.execute(
        select(LeaseClause).where(LeaseClause.lease_id == lease.id)
    )).scalars().all()
    reminders = (await db.execute(
        select(LeaseReminder).where(LeaseReminder.lease_id == lease.id).where(LeaseReminder.sent.is_(False))
    )).scalars().all()

    return {
        "found": True,
        "lease_id": str(lease.id),
        "property_address": lease.property_address,
        "lease_start": lease.lease_start.isoformat(),
        "lease_end": lease.lease_end.isoformat(),
        "monthly_rent": float(lease.monthly_rent) if lease.monthly_rent else None,
        "landlord_name": lease.landlord_name,
        "summary": lease.summary,
        "urgent": [{"type": c.clause_type, "note": c.claude_note} for c in clauses if c.severity == "urgent"],
        "flagged": [{"type": c.clause_type, "note": c.claude_note} for c in clauses if c.severity == "flag"],
        "standard_count": sum(1 for c in clauses if c.severity == "standard"),
        "upcoming_reminders": [r.remind_at.date().isoformat() for r in reminders],
    }


async def schedule_lease_reminder_handler(db: AsyncSession, **kw) -> dict:
    from app.models.real_estate import LeaseReminder
    lease = await _find_lease(db, kw["property_address"])
    if not lease:
        return {"scheduled": False, "message": f"No lease on file matching '{kw['property_address']}'."}
    remind_on = date.fromisoformat(kw["remind_on"])
    if remind_on <= date.today():
        return {"scheduled": False, "message": "That date has already passed — ask Cordia for a future date."}
    db.add(LeaseReminder(
        lease_id=lease.id,
        remind_at=datetime.combine(remind_on, datetime.min.time()),
        message=kw["message"],
    ))
    await db.commit()
    return {"scheduled": True, "property_address": lease.property_address, "remind_on": remind_on.isoformat()}


HANDLERS = {
    "save_lease": save_lease_handler,
    "list_leases": list_leases_handler,
    "get_lease_details": get_lease_details_handler,
    "schedule_lease_reminder": schedule_lease_reminder_handler,
    "flag_lease_clauses": flag_clauses_handler,
}
