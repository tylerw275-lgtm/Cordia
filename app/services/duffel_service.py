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
