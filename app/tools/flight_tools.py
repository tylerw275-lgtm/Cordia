import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import duffel_service

BOOKING_TOOL_SCHEMAS = [
    {
        "name": "get_booking_link",
        "description": (
            "Create a secure hosted booking link for Cordia when she wants to actually book "
            "a flight. The link opens Duffel's checkout where she searches or confirms the "
            "flight, picks seats/bags, and pays with her own card. Text her the link with a "
            "short note about which flight to pick. The link expires in 24 hours. After she "
            "books, you'll automatically receive the confirmation and can reference it later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Short label for this booking session (e.g. 'atl-mia-nov27')",
                },
            },
            "required": ["reference"],
        },
    },
]


async def get_booking_link_handler(db: AsyncSession, reference: str, **kwargs) -> dict:
    base = settings.public_base_url.rstrip("/")
    url = await duffel_service.create_link_session(
        reference=reference[:50],
        success_url=f"{base}/booking/complete",
        failure_url=f"{base}/booking/failed",
        abandonment_url=f"{base}/booking/abandoned",
    )
    if not url:
        return {
            "created": False,
            "message": "Couldn't create a booking link right now — try again in a moment.",
        }
    return {
        "created": True,
        "booking_url": url,
        "expires": "24 hours",
        "message": (
            "Text Cordia this link so she can complete the booking herself. Remind her "
            "which flight to select (airline, time, price) since the page starts at search."
        ),
    }

TOOL_SCHEMAS = [
    {
        "name": "search_flights",
        "description": "Search for available flights using Duffel. Use when Cordia asks about flights or travel options. Returns top options sorted by price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA airport code (e.g. ATL, ORD, LAX)"},
                "destination": {"type": "string", "description": "IATA airport code (e.g. MIA, SYD, LHR)"},
                "depart_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                "return_date": {"type": "string", "description": "Return date YYYY-MM-DD for round trips (omit for one-way)"},
                "adults": {"type": "integer", "description": "Number of adult travelers", "default": 1},
                "cabin": {
                    "type": "string",
                    "enum": ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
                    "description": "Cabin class preference",
                    "default": "ECONOMY",
                },
                "max_results": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["origin", "destination", "depart_date"],
        },
    },
    {
        "name": "watch_flight_price",
        "description": "Set up a price alert for a specific flight route. Cordia will receive an SMS when the price drops to or below the target, or drops more than 10% from the last check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA airport code"},
                "destination": {"type": "string", "description": "IATA airport code"},
                "depart_date": {"type": "string", "description": "Departure date YYYY-MM-DD"},
                "return_date": {"type": "string", "description": "Return date YYYY-MM-DD (optional)"},
                "cabin": {
                    "type": "string",
                    "enum": ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
                    "default": "ECONOMY",
                },
                "num_adults": {"type": "integer", "default": 1},
                "target_price": {
                    "type": "number",
                    "description": "Alert when total price (USD) is at or below this amount. Omit to alert on any 10%+ drop.",
                },
            },
            "required": ["origin", "destination", "depart_date"],
        },
    },
]


async def search_flights_handler(db: AsyncSession, **kwargs) -> dict:
    flights = await duffel_service.search_flights(**kwargs)
    if not flights:
        return {"found": False, "message": "No flights found for those parameters. Try different dates or airports."}
    return {"found": True, "flights": flights, "count": len(flights)}


async def watch_price_handler(db: AsyncSession, **kwargs) -> dict:
    from app.models.trips import FlightWatch
    watch = FlightWatch(
        origin=kwargs["origin"].upper(),
        destination=kwargs["destination"].upper(),
        depart_date=date.fromisoformat(kwargs["depart_date"]),
        return_date=date.fromisoformat(kwargs["return_date"]) if kwargs.get("return_date") else None,
        cabin_class=kwargs.get("cabin", "ECONOMY"),
        num_adults=kwargs.get("num_adults", 1),
        target_price=kwargs.get("target_price"),
        is_active=True,
    )
    db.add(watch)
    await db.commit()
    await db.refresh(watch)
    route = f"{watch.origin} → {watch.destination}"
    target_msg = f"${watch.target_price:.0f}" if watch.target_price else "any 10%+ drop"
    return {
        "watch_id": str(watch.id),
        "route": route,
        "depart_date": str(watch.depart_date),
        "cabin": watch.cabin_class,
        "alert_trigger": target_msg,
        "status": "monitoring_started",
    }
