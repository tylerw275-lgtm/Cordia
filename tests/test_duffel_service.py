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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_create_link_session_returns_url(mocker):
    mocker.patch(
        "httpx.AsyncClient.post",
        return_value=_FakeResponse({"data": {"url": "https://links.duffel.com/s/abc123"}}),
    )
    url = await duffel_service.create_link_session(
        reference="atl-mia-nov27",
        success_url="https://example.com/booking/complete",
        failure_url="https://example.com/booking/failed",
        abandonment_url="https://example.com/booking/abandoned",
    )
    assert url == "https://links.duffel.com/s/abc123"


@pytest.mark.asyncio
async def test_create_link_session_none_on_error(mocker):
    mocker.patch("httpx.AsyncClient.post", side_effect=Exception("API down"))
    url = await duffel_service.create_link_session(
        reference="x", success_url="s", failure_url="f", abandonment_url="a"
    )
    assert url is None


@pytest.mark.asyncio
async def test_get_offer_refreshes_price(mocker):
    mocker.patch(
        "httpx.AsyncClient.get",
        return_value=_FakeResponse({"data": MOCK_OFFER}),
    )
    offer = await duffel_service.get_offer("off_test_123")
    assert offer["price"] == 450.0
    assert offer["offer_id"] == "off_test_123"


@pytest.mark.asyncio
async def test_get_order_normalizes(mocker):
    order_payload = {
        "data": {
            "id": "ord_123",
            "booking_reference": "ABC123",
            "total_amount": "512.40",
            "total_currency": "USD",
            "slices": [
                {
                    "segments": [
                        {
                            "origin": {"iata_code": "ATL"},
                            "destination": {"iata_code": "MIA"},
                            "departing_at": "2026-11-27T08:00:00",
                        }
                    ]
                }
            ],
            "passengers": [{"given_name": "Cordia", "family_name": "Harrington"}],
        }
    }
    mocker.patch("httpx.AsyncClient.get", return_value=_FakeResponse(order_payload))
    order = await duffel_service.get_order("ord_123")
    assert order["booking_reference"] == "ABC123"
    assert order["origin"] == "ATL"
    assert order["destination"] == "MIA"
    assert order["passengers"] == ["Cordia Harrington"]
