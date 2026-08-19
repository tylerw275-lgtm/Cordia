"""Signal House SMS driver.

Sends outbound SMS through Signal House's REST API. Endpoint paths, auth
header, and payload field names are configurable because Signal House's
developer docs are dashboard-gated — the defaults below follow their
Twilio-style conventions and are finalized against the real docs.

VERIFY AGAINST DOCS (settings to adjust if their API differs):
- signalhouse_base_url      (default https://api.signalhouse.io)
- signalhouse_send_path     (default /message/send)
- auth: Authorization: Bearer <signalhouse_api_key> plus apiKey header
"""
import logging

import httpx

from app.config import settings
from app.services.twilio_service import _split_message

logger = logging.getLogger(__name__)


def _mask(phone: str) -> str:
    return f"{phone[:2]}*****{phone[-4:]}" if phone and len(phone) > 6 else "***"


async def send_sms(to: str, body: str) -> None:
    if not settings.signalhouse_api_key:
        raise RuntimeError("Signal House selected as sms_provider but SIGNALHOUSE_API_KEY is not set")
    from_number = settings.signalhouse_phone_number or settings.twilio_phone_number
    url = settings.signalhouse_base_url.rstrip("/") + settings.signalhouse_send_path
    headers = {
        "Authorization": f"Bearer {settings.signalhouse_api_key}",
        "apiKey": settings.signalhouse_api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in _split_message(body):
            payload = {"to": to, "from": from_number, "text": chunk}
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Signal House send_sms error to {_mask(to)}: {e}")
                raise
    logger.info(f"Signal House SMS sent to {_mask(to)} ({len(body)} chars)")
