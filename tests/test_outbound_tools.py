import pytest

from app.tools.outbound_tools import mask_address, send_outbound_handler


def test_mask_email():
    assert mask_address("kristen@gmail.com") == "k****@g****.com"
    assert mask_address(None) == "(none)"


def test_mask_phone():
    assert mask_address("+16155551234") == "+1*****1234"


@pytest.mark.asyncio
async def test_send_outbound_blocked_without_a_batch(db):
    # Nothing to send, and nothing sends.
    result = await send_outbound_handler(db, approval_code="000000")
    assert result["sent"] == 0


# ---------------------------------------------------------------------------
# The approval gate. It used to be a boolean the model set itself, so anything
# that could call the tool could also approve it — including content injected
# through the Naples inbox.
# ---------------------------------------------------------------------------

async def _draft(db, code="123456"):
    from app.models.outbound import OutboundMessage

    d = OutboundMessage(
        batch_id="test-batch", channel="email", to_name="Jordan Ellis",
        to_address="jordan@example.com", subject="Hi", body="Hello there",
        status="draft", approval_code=code,
    )
    db.add(d)
    await db.commit()
    return d


async def _owner_said(db, text):
    """Persist an inbound message as if Cordia texted it."""
    from app.services import claude_service

    conv = await claude_service.get_or_create_conversation(db, "+15551230000")
    await claude_service._persist_message(db, conv.id, "user", text)


@pytest.mark.asyncio
async def test_model_cannot_approve_its_own_batch(db, mocker):
    """The whole point: knowing the code is not enough. Cordia must have sent it."""
    send = mocker.patch("app.services.email_service.send_email", new=mocker.AsyncMock())
    await _draft(db, code="123456")

    result = await send_outbound_handler(db, batch_id="test-batch", approval_code="123456")

    assert result["blocked"] is True
    assert result["sent"] == 0
    assert not send.called


@pytest.mark.asyncio
async def test_wrong_code_is_rejected(db, mocker):
    send = mocker.patch("app.services.email_service.send_email", new=mocker.AsyncMock())
    await _draft(db, code="123456")
    await _owner_said(db, "999999")

    result = await send_outbound_handler(db, batch_id="test-batch", approval_code="999999")
    assert result["blocked"] is True
    assert not send.called


@pytest.mark.asyncio
async def test_sends_once_cordia_has_sent_the_code(db, mocker):
    send = mocker.patch(
        "app.services.email_service.send_email",
        new=mocker.AsyncMock(return_value={"sent": True}),
    )
    await _draft(db, code="123456")
    await _owner_said(db, "looks good, send them — 123456")

    result = await send_outbound_handler(db, batch_id="test-batch", approval_code="123456")
    assert result["sent"] and result["sent"][0]["to_name"] == "Jordan Ellis"
    assert send.called
