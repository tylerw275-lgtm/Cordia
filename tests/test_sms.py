import pytest


@pytest.mark.asyncio
async def test_inbound_sms_returns_twiml(client, mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    mocker.patch("app.services.claude_service.chat", return_value="Got it! I'll look into that now.")
    mocker.patch("app.services.twilio_service.send_sms")
    # Allow any number since cordia_phone_number is empty in test config
    mocker.patch("app.config.settings.cordia_phone_number", "")

    response = await client.post(
        "/webhook/sms",
        data={"From": "+15551234567", "Body": "Find me flights to Sydney", "MessageSid": "SM123"},
    )
    assert response.status_code == 200
    assert "<Response>" in response.text


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
