import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DUFFEL_BASE = "https://api.duffel.com"
DUFFEL_VERSION = "v2"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.duffel_access_token}",
        "Duffel-Version": DUFFEL_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _cabin(cabin: str) -> str:
    return {
        "ECONOMY": "economy",
        "PREMIUM_ECONOMY": "premium_economy",
        "BUSINESS": "business",
        "FIRST": "first",
    }.get(cabin.upper(), "economy")


def _normalize_offer(offer: dict) -> dict:
    slice_ = offer["slices"][0]
    segments = slice_["segments"]
    first_seg = segments[0]
    last_seg = segments[-1]
    carrier_code = first_seg["marketing_carrier"]["iata_code"]
    flight_number = f"{carrier_code}{first_seg['marketing_carrier_flight_number']}"
    cabin_name = "ECONOMY"
    try:
        cabin_name = offer["passengers"][0]["cabin_class_marketing_name"].upper()
    except (KeyError, IndexError):
        pass
    return {
        "price": float(offer["total_amount"]),
        "currency": offer["total_currency"],
        "carrier": carrier_code,
        "flight_number": flight_number,
        "depart_time": first_seg["departing_at"],
        "arrive_time": last_seg["arriving_at"],
        "destination_airport": last_seg["destination"]["iata_code"],
        "stops": len(segments) - 1,
        "duration": slice_["duration"],
        "cabin": cabin_name,
        "offer_id": offer["id"],
    }


async def get_offer(offer_id: str) -> dict | None:
    """Re-fetch a single offer to validate it's still bookable at the current price.

    Offers go stale in ~30 minutes; call this before presenting a 'book it' price.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{DUFFEL_BASE}/air/offers/{offer_id}",
                headers=_headers(),
            )
            resp.raise_for_status()
            return _normalize_offer(resp.json()["data"])
    except Exception as e:
        logger.error(f"Duffel get_offer error for {offer_id}: {e}")
        return None


async def create_link_session(
    reference: str,
    success_url: str,
    failure_url: str,
    abandonment_url: str,
    markup_rate: str | None = None,
) -> str | None:
    """Create a Duffel Links hosted search-and-checkout session.

    Returns the hosted URL the traveler opens to search, pick seats/bags, and pay
    with their own card on Duffel's page. Sessions expire after 24 hours.
    """
    payload = {
        "data": {
            "reference": reference,
            "success_url": success_url,
            "failure_url": failure_url,
            "abandonment_url": abandonment_url,
            "checkout_currency": "USD",
            "traveller_currency": "USD",
            "flights": {"enabled": True},
        }
    }
    if markup_rate:
        payload["data"]["markup_rate"] = markup_rate
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DUFFEL_BASE}/links/sessions",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["data"]["url"]
    except Exception as e:
        logger.error(f"Duffel create_link_session error: {e}")
        return None


async def get_order(order_id: str) -> dict | None:
    """Fetch a booked order (PNR, itinerary, totals) after an order.created webhook."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{DUFFEL_BASE}/air/orders/{order_id}",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            slices = data.get("slices") or []
            first_seg = slices[0]["segments"][0] if slices and slices[0].get("segments") else {}
            last_slice = slices[-1] if slices else {}
            last_seg = last_slice["segments"][-1] if last_slice.get("segments") else {}
            return {
                "order_id": data["id"],
                "booking_reference": data.get("booking_reference"),
                "total_amount": data.get("total_amount"),
                "currency": data.get("total_currency"),
                "origin": (first_seg.get("origin") or {}).get("iata_code"),
                "destination": (last_seg.get("destination") or {}).get("iata_code"),
                "depart_time": first_seg.get("departing_at"),
                "passengers": [
                    f"{p.get('given_name', '')} {p.get('family_name', '')}".strip()
                    for p in data.get("passengers", [])
                ],
            }
    except Exception as e:
        logger.error(f"Duffel get_order error for {order_id}: {e}")
        return None


async def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin: str = "ECONOMY",
    max_results: int = 10,
) -> list[dict]:
    slices = [{"origin": origin.upper(), "destination": destination.upper(), "departure_date": depart_date}]
    if return_date:
        slices.append({"origin": destination.upper(), "destination": origin.upper(), "departure_date": return_date})

    payload = {
        "data": {
            "slices": slices,
            "passengers": [{"type": "adult"} for _ in range(adults)],
            "cabin_class": _cabin(cabin),
            "return_offers": True,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DUFFEL_BASE}/air/offer_requests",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            offers = resp.json()["data"].get("offers", [])
            offers.sort(key=lambda o: float(o["total_amount"]))
            return [_normalize_offer(o) for o in offers[:max_results]]
    except Exception as e:
        logger.error(f"Duffel search_flights error: {e}")
        return []
