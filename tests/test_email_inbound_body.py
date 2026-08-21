"""Resend's email.received webhook carries metadata only — no body.

The handler used to read the body straight off the webhook payload, so every
reply Cordia sent produced an empty body, was dropped as "ignored_empty_body",
and returned a clean 200 that told Resend the message had been handled. Silent
data loss on a channel she was actively using.
"""
import base64
import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.services import email_service
from app.services.email_inbound import html_to_text

_KEY = base64.b64encode(b"0123456789abcdef").decode()
_SECRET = f"whsec_{_KEY}"
OWNER = "cordia@example.com"


def _signed(payload: dict):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = base64.b64encode(
        hmac.new(base64.b64decode(_KEY), b"msg_1." + ts.encode() + b"." + body, hashlib.sha256).digest()
    ).decode()
    return body, {
        "svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}",
        "content-type": "application/json",
    }


def _received(**data) -> dict:
    """A realistic email.received payload: metadata, and deliberately no body."""
    return {"type": "email.received", "data": {
        "email_id": "56761188-7520-42d8-8898-ff6fc54ce618",
        "from": OWNER, "to": ["cord@mail.example.com"],
        "subject": "Re: your note", **data,
    }}


@pytest.fixture(autouse=True)
def _resend_config(mocker):
    mocker.patch.object(settings, "email_webhook_signing_secret", _SECRET)
    mocker.patch.object(settings, "email_provider", "resend")
    mocker.patch.object(settings, "email_api_key", "re_test")
    mocker.patch.object(settings, "owner_email", OWNER)
    mocker.patch.object(settings, "enable_email", True)


async def _post(client, payload):
    body, headers = _signed(payload)
    return await client.post("/webhook/email", content=body, headers=headers)


@pytest.mark.asyncio
async def test_metadata_only_webhook_fetches_the_body_and_replies(client, mocker):
    fetch = mocker.patch.object(
        email_service, "fetch_received_email",
        return_value={"text": "What did the car service quote?", "html": None,
                      "from": OWNER, "subject": "Re: your note"},
    )
    chat = mocker.patch("app.services.claude_service.chat", return_value="Two options so far.")
    send = mocker.patch("app.services.email_service.send_email", return_value={"sent": True})

    r = await _post(client, _received())

    assert r.status_code == 200
    assert r.json()["status"] == "replied_as_owner"
    fetch.assert_awaited_once_with("56761188-7520-42d8-8898-ff6fc54ce618")
    assert "What did the car service quote?" in chat.call_args.args
    assert send.called


@pytest.mark.asyncio
async def test_html_only_message_is_converted_to_text(client, mocker):
    mocker.patch.object(
        email_service, "fetch_received_email",
        return_value={"text": None, "html": "<p>Book it</p><p>for <b>Friday</b></p>"},
    )
    chat = mocker.patch("app.services.claude_service.chat", return_value="Done.")
    mocker.patch("app.services.email_service.send_email", return_value={"sent": True})

    r = await _post(client, _received())

    assert r.status_code == 200
    assert "Book it\nfor Friday" in chat.call_args.args


@pytest.mark.asyncio
async def test_fetch_failure_returns_500_so_resend_retries(client, mocker):
    """A 200 here would tell Resend the message was handled and it would never
    be delivered again — the email would be lost for good."""
    mocker.patch.object(
        email_service, "fetch_received_email",
        side_effect=email_service.ReceivedEmailUnavailable("503 from Resend"),
    )
    chat = mocker.patch("app.services.claude_service.chat", return_value="never runs")

    r = await _post(client, _received())

    assert r.status_code == 500
    assert r.json()["status"] == "content_fetch_failed"
    assert not chat.called


@pytest.mark.asyncio
async def test_inline_body_is_used_without_fetching(client, mocker):
    """Gmail and other providers post the real body. Don't spend an API call."""
    fetch = mocker.patch.object(email_service, "fetch_received_email")
    chat = mocker.patch("app.services.claude_service.chat", return_value="ok")
    mocker.patch("app.services.email_service.send_email", return_value={"sent": True})

    r = await _post(client, _received(text="already here"))

    assert r.status_code == 200
    assert not fetch.called
    assert "already here" in chat.call_args.args


@pytest.mark.asyncio
async def test_delivery_receipts_never_trigger_a_fetch(client, mocker):
    """email.sent / email.delivered hit the same endpoint. Fetching on those
    would bill an API call and feed our own subject line back into a turn."""
    fetch = mocker.patch.object(email_service, "fetch_received_email")
    chat = mocker.patch("app.services.claude_service.chat")

    for event in ("email.sent", "email.delivered", "email.bounced"):
        r = await _post(client, {"type": event, "data": {"email_id": "abc", "from": OWNER}})
        assert r.status_code == 200

    assert not fetch.called
    assert not chat.called


@pytest.mark.asyncio
async def test_oversized_body_is_capped(client, mocker):
    from app.api.email import _MAX_BODY_CHARS
    mocker.patch.object(
        email_service, "fetch_received_email",
        return_value={"text": "x" * (_MAX_BODY_CHARS + 5000), "html": None},
    )
    chat = mocker.patch("app.services.claude_service.chat", return_value="ok")
    mocker.patch("app.services.email_service.send_email", return_value={"sent": True})

    await _post(client, _received())

    delivered = next(a for a in chat.call_args.args if isinstance(a, str) and a.startswith("x"))
    assert len(delivered) == _MAX_BODY_CHARS


# --- the HTML converter itself ---------------------------------------------

def test_html_to_text_drops_script_and_style():
    out = html_to_text("<style>p{color:red}</style><script>alert(1)</script><p>Real text</p>")
    assert out == "Real text"


def test_html_to_text_decodes_entities_and_keeps_breaks():
    assert html_to_text("<p>A &amp; B</p><br><p>C</p>") == "A & B\nC"


def test_html_to_text_on_empty_input():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""
