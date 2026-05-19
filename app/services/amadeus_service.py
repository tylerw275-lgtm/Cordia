import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    try:
        from amadeus import Client
    except ImportError as e:
        raise RuntimeError("amadeus package not installed. Run: pip install amadeus") from e
    return Client(
        client_id=settings.amadeus_client_id,
        client_secret=settings.amadeus_client_secret,
        hostname=settings.amadeus_environment,
    )


def _normalize_offer(offer: dict) -> dict:
    price = offer["price"]["grandTotal"]
    currency = offer["price"]["currency"]
    itineraries = offer["itineraries"]
    segments = itineraries[0]["segments"]
    first_seg = segments[0]
    last_seg = segments[-1]
    cabin = "ECONOMY"
    try:
        cabin = offer["travelerPricings"][0]["fareDetailsBySegment"][0]["cabin"]
    except (KeyError, IndexError):
        pass
    return {
        "price": float(price),
        "currency": currency,
        "carrier": first_seg["carrierCode"],
        "flight_number": f"{first_seg['carrierCode']}{first_seg['number']}",
        "depart_time": first_seg["departure"]["at"],
        "arrive_time": last_seg["arrival"]["at"],
        "destination_airport": last_seg["arrival"]["iataCode"],
        "stops": len(segments) - 1,
        "duration": itineraries[0]["duration"],
        "cabin": cabin,
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
    def _search():
        client = _get_client()
        params = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": depart_date,
            "adults": adults,
            "travelClass": cabin,
            "max": max_results,
            "currencyCode": "USD",
        }
        if return_date:
            params["returnDate"] = return_date
        response = client.shopping.flight_offers_search.get(**params)
        return response.data

    try:
        raw = await asyncio.to_thread(_search)
        return [_normalize_offer(offer) for offer in raw]
    except Exception as e:
        logger.error(f"Amadeus search_flights error: {e}")
        return []


async def get_cheapest_dates(
    origin: str,
    destination: str,
    departure_date: str,
) -> list[dict]:
    def _search():
        client = _get_client()
        response = client.shopping.flight_dates.get(
            origin=origin.upper(),
            destination=destination.upper(),
        )
        return response.data

    try:
        raw = await asyncio.to_thread(_search)
        return raw[:10] if raw else []
    except Exception as e:
        logger.error(f"Amadeus get_cheapest_dates error: {e}")
        return []
