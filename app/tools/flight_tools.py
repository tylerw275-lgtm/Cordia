import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import duffel_service, loyalty_service, travel_prefs

PREFERENCE_TOOL_SCHEMAS = [
    {
        "name": "get_travel_preferences",
        "description": (
            "Retrieve Cordia's stored travel preferences (non-stop, fastest vs cheapest, "
            "avoid-cities, cabin, airlines, loyalty programs). Call BEFORE any flight search. "
            "If nothing is stored yet, ask her the ranked-preference questions and save with "
            "set_travel_preferences."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_travel_preferences",
        "description": (
            "Save or update Cordia's travel preferences and loyalty programs. Use when first "
            "capturing them, and whenever she overrides one ('actually book the connection', "
            "'never route me through ORD') — that's how you learn to anticipate her."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nonstop_preferred": {"type": "boolean", "description": "She always prefers non-stop (default true)"},
                "priority": {"type": "string", "enum": ["fastest", "cheapest"], "description": "What wins after non-stop"},
                "avoid_cities": {"type": "array", "items": {"type": "string"}, "description": "Connection airports/cities to avoid (IATA codes preferred, e.g. ['ORD'])"},
                "preferred_airlines": {"type": "array", "items": {"type": "string"}, "description": "Preferred carriers (IATA codes)"},
                "cabin": {"type": "string", "enum": ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]},
                "loyalty_programs": {
                    "type": "object",
                    "description": "Programs she belongs to, e.g. {\"Delta SkyMiles\": \"member\", \"Marriott Bonvoy\": \"member\"}. Never store the actual member numbers here — just the program names.",
                },
            },
        },
    },
]


async def get_travel_preferences_handler(db: AsyncSession, **kwargs) -> dict:
    prefs = await travel_prefs.load_prefs(db)
    first_time = prefs.get("priority") is None and not prefs.get("loyalty_programs")
    return {
        "preferences": prefs,
        "first_time": first_time,
        "message": (
            "No preferences stored yet — ask her the ranked questions (non-stop assumed; "
            "fastest or cheapest; cities to avoid; cabin; loyalty programs) and save them."
            if first_time else "Apply these to the search; surface tradeoffs the filters hide."
        ),
    }


async def set_travel_preferences_handler(db: AsyncSession, **kwargs) -> dict:
    prefs = await travel_prefs.save_prefs(db, kwargs)
    return {"saved": True, "preferences": prefs}

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
    prefs = await travel_prefs.load_prefs(db)
    max_results = kwargs.pop("max_results", 5)
    if prefs.get("cabin") and "cabin" not in kwargs:
        kwargs["cabin"] = prefs["cabin"]
    # Her airline memberships ride along so fares reflect status and miles
    # accrue. Numbers are decrypted inside the service and handed straight to
    # Duffel — they are never returned to the model.
    loyalty = await loyalty_service.duffel_loyalty_accounts(db)
    # Pull a wider pool so preference filtering still leaves enough options
    try:
        flights = await duffel_service.search_flights(
            max_results=max(max_results * 3, 15), loyalty_accounts=loyalty or None, **kwargs
        )
    except duffel_service.DuffelUnavailable:
        return {
            "found": False,
            "unavailable": True,
            "message": (
                "Flight search is unavailable right now — this is a problem on our side, "
                "not with her search. Tell her you can't check fares at the moment and "
                "offer to try again shortly. Do NOT suggest different dates or airports."
            ),
        }
    if not flights:
        return {"found": False, "message": "No flights found for those parameters. Try different dates or airports."}
    matching, tradeoffs = travel_prefs.apply_preferences(flights, prefs)
    return {
        "found": True,
        "flights": matching[:max_results],
        "count": len(matching[:max_results]),
        "loyalty_applied": [a["airline_iata_code"] for a in loyalty],
        "preferences_applied": {
            "nonstop_preferred": prefs.get("nonstop_preferred", True),
            "priority": prefs.get("priority"),
            "avoid_cities": prefs.get("avoid_cities") or [],
        },
        "excluded_but_notable": tradeoffs,
        "note": (
            "excluded_but_notable are options her preferences filtered out that are notably "
            "faster or cheaper — mention each in one line so she can decide."
            if tradeoffs else None
        ),
    }


WATCH_MANAGEMENT_TOOL_SCHEMAS = [
    {
        "name": "list_flight_watches",
        "description": (
            "Show the flight routes Cordia is currently tracking, with the latest price "
            "seen, the price when tracking started, and the trend. Use when she asks what "
            "you're watching, how a fare is doing, or before adding a watch that may already exist."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stop_flight_watch",
        "description": "Stop tracking a route Cordia no longer cares about (e.g. she booked it or changed plans). Confirm which route you stopped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA code, e.g. BNA"},
                "destination": {"type": "string", "description": "IATA code, e.g. ORF"},
                "depart_date": {"type": "string", "description": "YYYY-MM-DD, if she has several watches on the same route"},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "check_watched_price_now",
        "description": (
            "Re-check the live price for a route Cordia is tracking right now, instead of "
            "waiting for the hourly check. Use when she asks 'what's that fare at today?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["origin", "destination"],
        },
    },
]


async def _active_watches(db: AsyncSession):
    from sqlalchemy import select
    from app.models.trips import FlightWatch
    result = await db.execute(
        select(FlightWatch).where(FlightWatch.is_active == True).order_by(FlightWatch.depart_date)  # noqa: E712
    )
    return result.scalars().all()


async def _watch_prices(db: AsyncSession, watch_id):
    """(first_seen, latest) lowest prices for a watch."""
    from sqlalchemy import select
    from app.models.trips import PriceSnapshot
    result = await db.execute(
        select(PriceSnapshot).where(PriceSnapshot.flight_watch_id == watch_id).order_by(PriceSnapshot.checked_at)
    )
    snaps = result.scalars().all()
    if not snaps:
        return None, None
    return float(snaps[0].lowest_price or 0), float(snaps[-1].lowest_price or 0)


async def list_flight_watches_handler(db: AsyncSession, **kwargs) -> dict:
    watches = await _active_watches(db)
    out = []
    for w in watches:
        first, latest = await _watch_prices(db, w.id)
        trend = None
        if first and latest:
            if latest < first:
                trend = f"down ${first - latest:.0f} since you started tracking"
            elif latest > first:
                trend = f"up ${latest - first:.0f} since you started tracking"
            else:
                trend = "unchanged"
        out.append({
            "route": f"{w.origin} to {w.destination}",
            "depart_date": w.depart_date.isoformat(),
            "return_date": w.return_date.isoformat() if w.return_date else None,
            "cabin": w.cabin_class,
            "target_price": float(w.target_price) if w.target_price else None,
            "latest_price": latest,
            "trend": trend,
            "last_checked": w.last_checked_at.isoformat() if w.last_checked_at else "not checked yet",
        })
    return {
        "count": len(out),
        "watches": out,
        "message": "Nothing being tracked yet — offer to watch a route for her." if not out else None,
    }


async def stop_flight_watch_handler(db: AsyncSession, **kwargs) -> dict:
    origin = (kwargs.get("origin") or "").upper()
    destination = (kwargs.get("destination") or "").upper()
    depart = kwargs.get("depart_date")
    stopped = []
    for w in await _active_watches(db):
        if w.origin.upper() == origin and w.destination.upper() == destination:
            if depart and w.depart_date.isoformat() != depart:
                continue
            w.is_active = False
            stopped.append(f"{w.origin} to {w.destination} on {w.depart_date.isoformat()}")
    await db.commit()
    if not stopped:
        return {"stopped": 0, "message": f"No active watch found for {origin} to {destination}."}
    return {"stopped": len(stopped), "routes": stopped}


async def check_watched_price_now_handler(db: AsyncSession, **kwargs) -> dict:
    origin = (kwargs.get("origin") or "").upper()
    destination = (kwargs.get("destination") or "").upper()
    for w in await _active_watches(db):
        if w.origin.upper() == origin and w.destination.upper() == destination:
            offers = await duffel_service.search_flights(
                origin=w.origin, destination=w.destination,
                depart_date=w.depart_date.isoformat(),
                return_date=w.return_date.isoformat() if w.return_date else None,
                adults=w.num_adults, cabin=w.cabin_class, max_results=5,
                loyalty_accounts=await loyalty_service.duffel_loyalty_accounts(db) or None,
            )
            if not offers:
                return {"found": False, "message": "No fares came back for that route right now."}
            prefs = await travel_prefs.load_prefs(db)
            matching, tradeoffs = travel_prefs.apply_preferences(offers, prefs)
            first, _ = await _watch_prices(db, w.id)
            best = matching[0] if matching else offers[0]
            return {
                "found": True,
                "route": f"{w.origin} to {w.destination}",
                "depart_date": w.depart_date.isoformat(),
                "best_now": best,
                "since_tracking_started": (f"${best['price'] - first:+.0f}" if first else None),
                "target_price": float(w.target_price) if w.target_price else None,
                "excluded_but_notable": tradeoffs,
            }
    return {"found": False, "message": f"You're not tracking {origin} to {destination} yet — offer to start."}


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
