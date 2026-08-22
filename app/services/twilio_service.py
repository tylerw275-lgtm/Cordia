import base64
import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

# Image types Claude vision accepts; other MMS media (vCards, audio) is ignored
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


async def download_media(url: str) -> tuple[bytes, str] | None:
    """Download an MMS media file from Twilio (auth required). Returns (bytes, content_type)."""
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, auth=auth)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            return resp.content, content_type
    except Exception as e:
        logger.error(f"Failed to download Twilio media {url}: {e}")
        return None


async def fetch_image_blocks(media: list[tuple[str, str]], max_images: int = 4) -> list[dict]:
    """Given [(url, content_type), ...] from Twilio, return Anthropic image blocks for images.

    Non-image media is skipped. Each block is base64-encoded for the Messages API.
    """
    blocks: list[dict] = []
    for url, declared_type in media[:max_images]:
        if declared_type and declared_type.lower() not in SUPPORTED_IMAGE_TYPES:
            continue
        result = await download_media(url)
        if not result:
            continue
        data, content_type = result
        media_type = content_type if content_type in SUPPORTED_IMAGE_TYPES else (declared_type or "image/jpeg")
        if media_type not in SUPPORTED_IMAGE_TYPES:
            continue
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        })
    return blocks


def _get_client():
    from twilio.rest import Client
    # Prefer API Key auth when available (more secure, scoped credentials)
    if settings.twilio_api_key_sid and settings.twilio_api_key_secret:
        return Client(settings.twilio_api_key_sid, settings.twilio_api_key_secret,
                      settings.twilio_account_sid)
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
    # With no auth token the HMAC key is the empty string, so anyone could
    # compute a valid signature — and a forged inbound SMS is processed with
    # owner privileges, or hits the STOP/HELP branch and sends an SMS to an
    # attacker-chosen number. Refuse rather than verify against "".
    if not settings.twilio_auth_token:
        logger.error("Twilio webhook rejected: TWILIO_AUTH_TOKEN is not configured")
        raise HTTPException(status_code=403, detail="Signature verification unavailable")
    signature = request.headers.get("X-Twilio-Signature", "")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    if not validate_twilio_signature(url, form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
