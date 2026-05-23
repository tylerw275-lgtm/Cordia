import pytest

from app.services import amadeus_service


MOCK_OFFER = {
    "id": "1",
    "price": {"grandTotal": "450.00", "currency": "USD"},
    "itineraries": [
        {
            "duration": "PT5H30M",
            "segments": [
                {
                    "carrierCode": "DL",
                    "number": "123",
                    "departure": {"at": "2026-11-27T08:00:00", "iataCode": "ATL"},
                    "arrival": {"at": "2026-11-27T13:30:00", "iataCode": "MIA"},
                }
            ],
        }
    ],
    "travelerPricings": [{"fareDetailsBySegment": [{"cabin": "ECONOMY"}]}],
}


@pytest.mark.asyncio
async def test_normalize_offer():
    result = amadeus_service._normalize_offer(MOCK_OFFER)
    assert result["price"] == 450.0
    assert result["carrier"] == "DL"
    assert result["stops"] == 0
    assert result["cabin"] == "ECONOMY"
    assert result["currency"] == "USD"


@pytest.mark.asyncio
async def test_search_flights_returns_empty_on_error(mocker):
    mocker.patch("app.services.amadeus_service._get_client", side_effect=Exception("API down"))
    results = await amadeus_service.search_flights(
        origin="ATL", destination="MIA", depart_date="2026-11-27"
    )
    assert results == []
