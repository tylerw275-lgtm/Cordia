"""Inbound email drives a model turn with tools attached, so the webhook must
prove the request really came from Resend."""
import base64
import hashlib
import hmac
import time

from app.api.email import _verify_svix_signature

_KEY = base64.b64encode(b"0123456789abcdef").decode()
_SECRET = f"whsec_{_KEY}"
_BODY = b'{"type":"email.received"}'


def _headers(body=_BODY, msg_id="msg_1", ts=None):
    ts = ts or str(int(time.time()))
    sig = base64.b64encode(
        hmac.new(base64.b64decode(_KEY), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


def test_valid_signature_accepted():
    assert _verify_svix_signature(_SECRET, _headers(), _BODY) is True


def test_tampered_body_rejected():
    assert _verify_svix_signature(_SECRET, _headers(), b'{"type":"evil"}') is False


def test_replayed_old_delivery_rejected():
    old = str(int(time.time()) - 3600)
    assert _verify_svix_signature(_SECRET, _headers(ts=old), _BODY) is False


def test_missing_headers_rejected():
    assert _verify_svix_signature(_SECRET, {}, _BODY) is False


def test_multiple_signatures_one_valid_accepted():
    h = _headers()
    h["svix-signature"] = f"v1,ZmFrZQ== {h['svix-signature']}"
    assert _verify_svix_signature(_SECRET, h, _BODY) is True
