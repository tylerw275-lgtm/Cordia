from app.tools.contact_tools import _format_program_number, _normalize_phone_e164, build_invite_message


def test_format_program_number():
    assert _format_program_number("+16155021290") == "(615) 502-1290"
    assert _format_program_number("6155021290") == "(615) 502-1290"


def test_normalize_phone_e164():
    assert _normalize_phone_e164("(615) 555-1234") == "+16155551234"
    assert _normalize_phone_e164("16155551234") == "+16155551234"
    assert _normalize_phone_e164("123") is None
    assert _normalize_phone_e164(None) is None


def test_invite_message_contains_link_number_and_optout():
    msg = build_invite_message("Susan", "https://example.com/consent", "(615) 502-1290")
    assert "Susan" in msg
    assert "https://example.com/consent" in msg
    assert "(615) 502-1290" in msg
    assert "START" in msg and "STOP" in msg
