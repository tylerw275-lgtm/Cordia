import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.model_body import model_kwargs, required
from app.models.real_estate import Lease, LeaseClause, LeaseReminder

router = APIRouter(prefix="/api/v1/leases", tags=["real_estate"])


@router.get("")
async def list_leases(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Lease).order_by(Lease.lease_end))
    leases = result.scalars().all()
    return {
        "leases": [
            {"id": str(l.id), "address": l.property_address, "status": l.status,
             "lease_end": l.lease_end.isoformat(), "tenant": l.tenant_name}
            for l in leases
        ]
    }


@router.post("")
async def create_lease(body: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict:
    required(body, "property_address", "lease_start", "lease_end")
    try:
        body["lease_start"] = date.fromisoformat(body["lease_start"])
        body["lease_end"] = date.fromisoformat(body["lease_end"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="lease_start and lease_end must be YYYY-MM-DD")
    lease = Lease(**model_kwargs(Lease, body))
    db.add(lease)

    # Auto-create renewal reminder
    notice_days = body.get("renewal_notice_days", 60)
    remind_date = datetime.combine(lease.lease_end, datetime.min.time()) - timedelta(days=notice_days)
    reminder = LeaseReminder(
        lease_id=lease.id,
        remind_at=remind_date,
        message=f"Lease at {body['property_address']} expires in {notice_days} days on {body['lease_end']}. Review renewal options.",
    )
    db.add(reminder)
    await db.commit()
    await db.refresh(lease)
    return {"id": str(lease.id), "address": lease.property_address, "reminder_set": True}


@router.get("/{lease_id}")
async def get_lease(lease_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Lease).where(Lease.id == lease_id))
    lease = result.scalar_one_or_none()
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")

    clauses_result = await db.execute(select(LeaseClause).where(LeaseClause.lease_id == lease_id))
    clauses = clauses_result.scalars().all()

    return {
        "id": str(lease.id),
        "address": lease.property_address,
        "tenant": lease.tenant_name,
        "landlord": lease.landlord_name,
        "lease_start": lease.lease_start.isoformat(),
        "lease_end": lease.lease_end.isoformat(),
        "monthly_rent": float(lease.monthly_rent) if lease.monthly_rent else None,
        "status": lease.status,
        "summary": lease.summary,
        "clauses": [
            {"type": c.clause_type, "severity": c.severity, "content": c.content, "note": c.claude_note}
            for c in clauses
        ],
    }


@router.post("/{lease_id}/analyze")
async def analyze_lease(lease_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Trigger Claude to analyze the lease text and store flagged clauses."""
    result = await db.execute(select(Lease).where(Lease.id == lease_id))
    lease = result.scalar_one_or_none()
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    if not lease.raw_text:
        raise HTTPException(status_code=400, detail="No lease text to analyze. Add raw_text first.")

    from app.services.lease_service import analyze_lease_text
    summary, clauses = await analyze_lease_text(lease_id=str(lease.id), raw_text=lease.raw_text, db=db)
    return {"lease_id": str(lease_id), "summary": summary, "clauses_flagged": len(clauses)}


@router.get("/{lease_id}/clauses")
async def get_clauses(lease_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(LeaseClause)
        .where(LeaseClause.lease_id == lease_id)
        .order_by(LeaseClause.severity)
    )
    clauses = result.scalars().all()
    return {
        "lease_id": str(lease_id),
        "clauses": [
            {"type": c.clause_type, "severity": c.severity, "content": c.content, "note": c.claude_note}
            for c in clauses
        ],
    }
