import pytest

from app.services import duffel_service


MOCK_OFFER = {
    "id": "off_test_123",
    "total_amount": "450.00",
    "total_currency": "USD",
    "slices": [
        {
            "duration": "PT5H30M",
            "segments": [
                {
                    "marketing_carrier": {"iata_code": "DL", "name": "Delta"},
                    "marketing_carrier_flight_number": "123",
                    "departing_at": "2026-11-27T08:00:00",
                    "arriving_at": "2026-11-27T13:30:00",
                    "origin": {"iata_code": "ATL"},
                    "destination": {"iata_code": "MIA"},
                }
            ],
        }
    ],
    "passengers": [{"cabin_class_marketing_name": "Economy"}],
}


@pytest.mark.asyncio
async def test_normalize_offer():
    result = duffel_service._normalize_offer(MOCK_OFFER)
    assert result["price"] == 450.0
    assert result["carrier"] == "DL"
    assert result["flight_number"] == "DL123"
    assert result["stops"] == 0
    assert result["currency"] == "USD"
    assert result["offer_id"] == "off_test_123"


@pytest.mark.asyncio
async def test_search_flights_returns_empty_on_error(mocker):
    mocker.patch("httpx.AsyncClient.post", side_effect=Exception("API down"))
    results = await duffel_service.search_flights(
        origin="ATL", destination="MIA", depart_date="2026-11-27"
    )
    assert results == []
