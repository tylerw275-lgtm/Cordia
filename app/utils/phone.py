import re


def normalize_phone(phone: str | None) -> str:
    """Reduce a phone number to its last 10 digits for comparison.

    Handles formats like '+16158539483', '6158539483', '(615) 853-9483'.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def phones_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_phone(a), normalize_phone(b)
    return len(na) == 10 and na == nb
