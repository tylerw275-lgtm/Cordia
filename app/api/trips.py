import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.trips import FlightWatch, PriceSnapshot, Trip

router = APIRouter(prefix="/api/v1/trips", tags=["trips"])


@router.get("")
async def list_trips(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Trip).order_by(Trip.depart_date))
    trips = result.scalars().all()
    return {
        "trips": [
            {"id": str(t.id), "title": t.title, "origin": t.origin, "destination": t.destination,
             "depart_date": t.depart_date.isoformat(), "status": t.status}
            for t in trips
        ]
    }


@router.post("")
async def create_trip(body: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict:
    body["depart_date"] = date.fromisoformat(body["depart_date"])
    if "return_date" in body and body["return_date"]:
        body["return_date"] = date.fromisoformat(body["return_date"])
    trip = Trip(**body)
    db.add(trip)
    await db.commit()
    await db.refresh(trip)
    return {"id": str(trip.id), "title": trip.title}


@router.get("/{trip_id}")
async def get_trip(trip_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return {
        "id": str(trip.id),
        "title": trip.title,
        "origin": trip.origin,
        "destination": trip.destination,
        "depart_date": trip.depart_date.isoformat(),
        "return_date": trip.return_date.isoformat() if trip.return_date else None,
        "preferences": trip.preferences,
        "activities": trip.activities,
        "packing_list": trip.packing_list,
        "memory_ideas": trip.memory_ideas,
        "status": trip.status,
    }


@router.get("/{trip_id}/price-history")
async def get_price_history(trip_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(FlightWatch).where(FlightWatch.trip_id == trip_id)
    )
    watches = result.scalars().all()
    history = []
    for watch in watches:
        snaps = await db.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.flight_watch_id == watch.id)
            .order_by(PriceSnapshot.checked_at)
        )
        history.extend([
            {"checked_at": s.checked_at.isoformat(), "price": float(s.lowest_price or 0), "alerted": s.alerted}
            for s in snaps.scalars().all()
        ])
    return {"trip_id": str(trip_id), "price_history": history}
