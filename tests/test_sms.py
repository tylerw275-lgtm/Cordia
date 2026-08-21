import pytest


@pytest.mark.asyncio
async def test_inbound_sms_returns_twiml(client, mocker):
    """Only what the name says: the webhook acknowledges immediately with empty
    TwiML, because the provider retries a slow response and an AI round trip is
    slow. The reply itself goes out later, from the background task.

    This used to be called the "inbound SMS works" test and mocked chat as if it
    were checking the answer. It never was — the sender resolved to unknown, the
    mocked chat was never called, and the assertion below passes either way. The
    conversation is covered in test_conversation_end_to_end.py.
    """
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    chat = mocker.patch("app.services.claude_service.chat", new=mocker.AsyncMock())
    mocker.patch("app.config.settings.cordia_phone_number", "")

    response = await client.post(
        "/webhook/sms",
        data={"From": "+15551234567", "Body": "Find me flights to Sydney", "MessageSid": "SM123"},
    )
    assert response.status_code == 200
    assert "<Response>" in response.text
    assert not chat.called, "the webhook answered inline instead of acknowledging first"


@pytest.mark.asyncio
async def test_sms_from_unknown_number_rejected(client, mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    mocker.patch("app.config.settings.cordia_phone_number", "+19999999999")

    response = await client.post(
        "/webhook/sms",
        data={"From": "+15550000000", "Body": "Hello", "MessageSid": "SM456"},
    )
    assert response.status_code == 200
    # Should return empty TwiML without calling Claude
    assert "<Response>" in response.text


# ---------------------------------------------------------------------------
# End-to-end: the exact exchange that failed in production.
# ---------------------------------------------------------------------------

class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text

    def model_dump(self):
        return {"type": "text", "text": self.text}


class _Reply:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [_Text(text)]


@pytest.mark.asyncio
async def test_family_roster_reaches_the_model(db, seed_doc, mocker):
    """"What do you know about my family" must arrive at the model with the
    family already in the system prompt — the production bug was an empty
    table, so Cord truthfully answered that it had no profiles.

    Calls _process_inbound directly rather than posting the webhook: the
    webhook defers to a background task that opens its own session from the
    module-level engine, which isn't bound to the test event loop.
    """
    from app.api.sms import _process_inbound
    from app.services import claude_service
    from app.services.family_seed import seed_family

    await seed_family(db, seed_doc)

    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(return_value=_Reply("Three sons: Dominic, Elliot and Theodore.")),
    )
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    mocker.patch("app.config.settings.cordia_phone_number", "+15551234567")

    await _process_inbound(db, "+15551234567", "What do you know about my family", [])

    create.assert_awaited()
    system = " ".join(b["text"] for b in create.await_args.kwargs["system"])
    assert "FAMILY ROSTER" in system
    for member in seed_doc.members:
        assert member.name in system
    # ...and no contact details ride along. (The roster block's exclusion of
    # aliases is asserted directly in tests/test_system_prompt.py.)
    for member in seed_doc.members:
        if member.phone:
            assert member.phone not in system
