import pytest

from app.services import email_service
from app.tools import email_tools


def test_markdown_to_html_renders_structure():
    html = email_service.markdown_to_html("## Options\n- Delta $420\n- United $510\n\n**Best:** Delta")
    assert "<h2>Options</h2>" in html
    assert "<li>Delta $420</li>" in html
    assert "<strong>Best:</strong>" in html


@pytest.mark.asyncio
async def test_send_email_skips_when_unconfigured(mocker):
    mocker.patch("app.config.settings.email_provider", "gmail")
    mocker.patch("app.config.settings.email_address", "")
    mocker.patch("app.config.settings.email_app_password", "")
    result = await email_service.send_email("x@y.com", "Hi", "body")
    assert result["sent"] is False
    assert result["reason"] == "email_not_configured"


def test_from_address_derives_from_gmail(mocker):
    mocker.patch("app.config.settings.email_from", "")
    mocker.patch("app.config.settings.email_from_name", "Cordia")
    mocker.patch("app.config.settings.email_address", "cordiaassistant@gmail.com")
    assert email_service.from_address() == "Cordia <cordiaassistant@gmail.com>"


@pytest.mark.asyncio
async def test_send_email_gmail_uses_smtp(mocker):
    mocker.patch("app.config.settings.email_provider", "gmail")
    mocker.patch("app.config.settings.email_address", "bot@gmail.com")
    mocker.patch("app.config.settings.email_app_password", "apppassword123")
    smtp_mock = mocker.patch("app.services.email_service._send_gmail_sync")
    result = await email_service.send_email("cordia@inbox.com", "Trip", "## Plan\n- day 1")
    assert result["sent"] is True
    assert smtp_mock.called


def test_strip_quoted_history():
    from app.services import email_inbound
    body = "Yes do that\n\nOn Mon, Jun 15 2026, Cordia wrote:\n> the original message"
    assert email_inbound.strip_quoted(body) == "Yes do that"


@pytest.mark.asyncio
async def test_send_report_email_handler(db, mocker):
    mocker.patch("app.config.settings.owner_email", "cordia@inbox.com")
    send_mock = mocker.patch(
        "app.services.email_service.send_email", return_value={"sent": True, "id": "e1"}
    )
    result = await email_tools.send_report_email_handler(
        db=db, subject="Flight comparison", body_markdown="## Options\n- A\n- B"
    )
    assert result["sent"] is True
    assert send_mock.called
    _, kwargs = send_mock.call_args
    assert kwargs["to"] == "cordia@inbox.com"


@pytest.mark.asyncio
async def test_send_report_email_handler_no_email_on_file(db, mocker):
    mocker.patch("app.config.settings.owner_email", "")
    result = await email_tools.send_report_email_handler(
        db=db, subject="x", body_markdown="y"
    )
    assert result["sent"] is False


@pytest.mark.asyncio
async def test_inbound_email_from_owner_replies(client, mocker):
    mocker.patch("app.config.settings.owner_email", "cordia@inbox.com")
    mocker.patch("app.config.settings.cordia_phone_number", "+15551112222")
    mocker.patch("app.config.settings.email_inbound_secret", "")
    chat_mock = mocker.patch(
        "app.services.claude_service.chat", return_value="Here are 3 options..."
    )
    send_mock = mocker.patch(
        "app.services.email_service.send_email", return_value={"sent": True}
    )

    response = await client.post(
        "/webhook/email",
        json={"from": "Cordia <cordia@inbox.com>", "subject": "Flights to Italy", "text": "find me options"},
    )
    assert response.status_code == 200
    assert chat_mock.called
    _, ckwargs = chat_mock.call_args
    assert ckwargs["sender_role"] == "owner"
    # Replies back to the sender, threaded
    assert send_mock.called
    _, skwargs = send_mock.call_args
    assert skwargs["to"] == "cordia@inbox.com"
    assert skwargs["subject"].lower().startswith("re:")


@pytest.mark.asyncio
async def test_inbound_email_unknown_sender_ignored(client, mocker):
    mocker.patch("app.config.settings.owner_email", "cordia@inbox.com")
    chat_mock = mocker.patch("app.services.claude_service.chat", return_value="x")

    response = await client.post(
        "/webhook/email",
        json={"from": "stranger@nowhere.com", "subject": "hi", "text": "hello"},
    )
    assert response.status_code == 200
    assert not chat_mock.called
