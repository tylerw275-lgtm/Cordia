"""One phone formatter, and the number it used to mangle.

There were three, all hardcoding +1 in slightly different ways. The worst was in
`add_contact`, which mapped anything up to eleven digits onto "+1" plus the last
ten. A Paris mobile, 33 6 12 34 56 78, came out as +1612345678 — a US number
that does not exist — and it was saved that way. Every vendor, guide or driver
Cordia might collect while planning a trip outside North America was quietly
rewritten on the way into the address book.
"""
import pytest

from app.utils.phone import normalize_phone, phones_match, to_e164


@pytest.mark.parametrize("written,expected", [
    ("6155551234", "+16155551234"),
    ("(615) 555-1234", "+16155551234"),
    ("615-555-1234", "+16155551234"),
    ("16155551234", "+16155551234"),
    ("+1 615 555 1234", "+16155551234"),
])
def test_a_us_number_still_gets_its_country_code(written, expected):
    assert to_e164(written) == expected


@pytest.mark.parametrize("written,expected", [
    ("+33 6 12 34 56 78", "+33612345678"),      # Paris
    ("+255 754 123 456", "+255754123456"),      # Arusha
    ("+27 21 555 0199", "+27215550199"),        # Cape Town
    ("+44 20 7946 0958", "+442079460958"),      # London
])
def test_an_international_number_survives_being_saved(written, expected):
    assert to_e164(written) == expected


def test_a_leading_plus_is_believed_over_any_guess():
    """It is the caller stating a country code. Overriding it is how a French
    number became an American one."""
    assert to_e164("+33612345678") == "+33612345678"
    assert to_e164("+442079460958") == "+442079460958"


@pytest.mark.parametrize("junk", ["", None, "123", "call me", "555-1234"])
def test_something_that_is_not_a_number_is_rejected_rather_than_padded(junk):
    assert to_e164(junk) is None


def test_the_default_country_is_configurable_rather_than_baked_in():
    assert to_e164("2155550199", default_country="44") == "+442155550199"


def test_every_caller_uses_the_same_formatter():
    """Three copies is how they drifted apart in the first place."""
    from app.tools import contact_tools

    assert contact_tools._normalize_phone_e164 is to_e164


# --- the comparison helper, which is a different job ------------------------

def test_matching_still_ignores_formatting():
    """Consent rows are +E164 and family profiles carry bare digits."""
    assert phones_match("+16155551234", "6155551234")
    assert phones_match("(615) 555-1234", "16155551234")
    assert not phones_match("+16155551234", "+16155559999")


def test_matching_needs_a_full_number_on_both_sides():
    """A short string must never match everything."""
    assert not phones_match("1234", "1234")
    assert not phones_match("", "")
    assert normalize_phone("1234") == "1234"
