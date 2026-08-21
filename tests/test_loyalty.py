import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.services import loyalty_service


@pytest.fixture(autouse=True)
def _key():
    original = settings.loyalty_encryption_key
    settings.loyalty_encryption_key = Fernet.generate_key().decode()
    yield
    settings.loyalty_encryption_key = original


def test_roundtrip():
    token = loyalty_service.encrypt("9017345662")
    assert token != "9017345662"
    assert loyalty_service.decrypt(token) == "9017345662"


def test_storage_fails_closed_without_a_key():
    settings.loyalty_encryption_key = ""
    with pytest.raises(loyalty_service.EncryptionUnavailable):
        loyalty_service.encrypt("9017345662")


def test_card_numbers_are_recognised():
    assert loyalty_service.looks_like_a_card_number("4111111111111111")
    assert loyalty_service.looks_like_a_card_number("4111 1111 1111 1111")
    # A frequent-flyer number is shorter and must not be mistaken for a card
    assert not loyalty_service.looks_like_a_card_number("9017345662")
    assert not loyalty_service.looks_like_a_card_number("")


def test_safe_view_never_exposes_the_number():
    from app.models.loyalty import LoyaltyAccount

    account = LoyaltyAccount(
        program_name="Delta SkyMiles", program_type="airline",
        airline_iata_code="DL", account_number_encrypted=loyalty_service.encrypt("9017345662"),
        last_four="5662",
    )
    view = loyalty_service.safe_view(account)
    assert view["last_four"] == "5662"
    assert view["number_on_file"] is True
    assert "9017345662" not in str(view)
    assert "account_number_encrypted" not in view
