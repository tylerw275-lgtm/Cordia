import re


def normalize_phone(phone: str | None) -> str:
    """Reduce a phone number to its last 10 digits for comparison.

    Handles formats like '+15555550123', '5555550123', '(555) 555-0123'.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def phones_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_phone(a), normalize_phone(b)
    return len(na) == 10 and na == nb


def to_e164(phone: str | None, default_country: str = "1") -> str | None:
    """Format a number for storage and sending, or None if it cannot be one.

    There were three of these, and three hardcoded +1 in different ways. The
    worst was `add_contact`, which mapped anything up to eleven digits onto
    `+1` + the last ten: a Paris number, 33 6 12 34 56 78, came out as
    +1612345678, and every African vendor or guide Cordia might collect for a
    trip was silently rewritten into a US number that does not exist.

    The rules, in order:
      - a leading '+' is the caller telling us the country code. Believe it.
      - eleven digits starting with 1 is North American.
      - ten digits has no country code, so `default_country` supplies one.
      - anything longer already carries its own country code.
    """
    if not phone:
        return None
    explicit = phone.strip().startswith("+")
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    if not explicit and len(digits) == 10:
        return f"+{default_country}{digits}"
    return f"+{digits}"
