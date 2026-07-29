import base64

import pytest

from app.services import twilio_service


@pytest.mark.asyncio
async def test_fetch_image_blocks_encodes_and_filters(mocker):
    async def fake_download(url):
        return (b"\xff\xd8\xff", "image/jpeg")

    mocker.patch("app.services.twilio_service.download_media", side_effect=fake_download)

    media = [
        ("https://x/img0", "image/jpeg"),
        ("https://x/vcard", "text/x-vcard"),  # non-image, should be skipped
    ]
    blocks = await twilio_service.fetch_image_blocks(media)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    assert block["source"]["data"] == base64.standard_b64encode(b"\xff\xd8\xff").decode()


@pytest.mark.asyncio
async def test_fetch_image_blocks_respects_max(mocker):
    async def fake_download(url):
        return (b"abc", "image/png")

    mocker.patch("app.services.twilio_service.download_media", side_effect=fake_download)
    media = [(f"https://x/{i}", "image/png") for i in range(10)]
    blocks = await twilio_service.fetch_image_blocks(media, max_images=4)
    assert len(blocks) == 4


@pytest.mark.asyncio
async def test_webhook_passes_images_to_chat(client, mocker):
    mocker.patch("app.api.sms.twilio_service.verify_twilio_request")
    mocker.patch("app.services.twilio_service.send_sms")
    mocker.patch("app.config.settings.cordia_test_phone_number", "+15551112222")
    mocker.patch(
        "app.api.sms.twilio_service.fetch_image_blocks",
        return_value=[{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}],
    )
    chat_mock = mocker.patch("app.services.claude_service.chat", return_value="I read your calendar.")

    response = await client.post(
        "/webhook/sms",
        data={
            "From": "+15551112222",
            "Body": "add these dates",
            "MessageSid": "SM999",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/img0",
            "MediaContentType0": "image/jpeg",
        },
    )
    assert response.status_code == 200
    assert chat_mock.called
    _, kwargs = chat_mock.call_args
    assert kwargs["sender_role"] == "owner"
    assert len(kwargs["images"]) == 1
    assert kwargs["images"][0]["type"] == "image"
