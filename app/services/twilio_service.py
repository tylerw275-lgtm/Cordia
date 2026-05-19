import hashlib
import hmac
import logging
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    from twilio.rest import Client
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _split_message(body: str, max_length: int = 1500) -> list[str]:
    if len(body) <= max_length:
        return [body]
    chunks = []
    while body:
        chunk = body[:max_length]
        last_break = max(chunk.rfind("\n"), chunk.rfind(". "), chunk.rfind(" "))
        if last_break > max_length // 2:
            chunk = body[:last_break + 1]
        chunks.append(chunk.strip())
        body = body[len(chunk):].strip()
    return chunks


async def send_sms(to: str, body: str) -> None:
    client = _get_client()
    chunks = _split_message(body)
    for chunk in chunks:
        try:
            client.messages.create(
                to=to,
                from_=settings.twilio_phone_number,
                body=chunk,
            )
        except Exception as e:
            logger.error(f"Twilio send_sms error to {to}: {e}")
            raise


def validate_twilio_signature(url: str, params: dict, signature: str) -> bool:
    """Validate that a request came from Twilio using HMAC-SHA1."""
    auth_token = settings.twilio_auth_token
    s = url
    if params:
        s += "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    import base64
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def verify_twilio_request(request: Request, form_data: dict) -> None:
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    if not validate_twilio_signature(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
