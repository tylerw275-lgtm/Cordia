"""Signal House SMS driver.

Verified against Signal House's own API client (app2.signalhouse.io/docs):
- Send:  POST https://v2.signalhouse.io/message/sms
         headers: Authorization: Bearer <apiKey>
         body: {senderPhoneNumber, recipientPhoneNumber: [array], messageBody}
         Phone numbers are digits-only (no '+'), e.g. "16292259067".
- Inbound webhook events arrive as {event: "MESSAGE_RECEIVED",
  metaData: {Message: {senderPhoneNumber, messageBody, ...}}} — handled in
  app/api/sms.py.
"""
import logging
import re

import httpx

from app.config import settings
from app.services.twilio_service import _split_message

logger = logging.getLogger(__name__)


def _digits(phone: str) -> str:
    """Signal House wants digits only, with country code: '+1 (629) 225-9067' -> '16292259067'."""
    d = re.sub(r"\D", "", phone or "")
    return d if len(d) != 10 else f"1{d}"


def _mask(phone: str) -> str:
    return f"{phone[:2]}*****{phone[-4:]}" if phone and len(phone) > 6 else "***"


async def send_sms(to: str, body: str) -> None:
    if not settings.signalhouse_api_key:
        raise RuntimeError("Signal House selected as sms_provider but SIGNALHOUSE_API_KEY is not set")
    sender = _digits(settings.signalhouse_phone_number or settings.twilio_phone_number)
    url = settings.signalhouse_base_url.rstrip("/") + settings.signalhouse_send_path
    headers = {
        "Authorization": f"Bearer {settings.signalhouse_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in _split_message(body):
            payload = {
                "senderPhoneNumber": sender,
                "recipientPhoneNumber": [_digits(to)],
                "messageBody": chunk,
            }
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Signal House send_sms error to {_mask(to)}: {e}")
                raise
    logger.info(f"Signal House SMS sent to {_mask(to)} ({len(body)} chars)")
