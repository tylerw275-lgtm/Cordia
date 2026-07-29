import pytest

from app.tools.outbound_tools import mask_address, send_outbound_handler


def test_mask_email():
    assert mask_address("kristen@gmail.com") == "k****@g****.com"
    assert mask_address(None) == "(none)"


def test_mask_phone():
    assert mask_address("+16155551234") == "+1*****1234"


@pytest.mark.asyncio
async def test_send_outbound_blocked_without_cordias_approval():
    # The approval gate fires before any DB access — nothing sends, ever,
    # unless Cordia explicitly approved in conversation.
    result = await send_outbound_handler(None, approved_by_cordia=False)
    assert result["blocked"] is True
    assert result["sent"] == 0

    result = await send_outbound_handler(None, approved_by_cordia=None)
    assert result["blocked"] is True
