"""Provider-agnostic SMS sending.

All outbound texting in the app goes through send_sms() here; the active
provider is chosen by settings.sms_provider ("twilio" or "signalhouse").
Message content and STOP/START/HELP keyword handling live above this layer.

Opt-out enforcement lives *here*, deliberately: every proactive job and webhook
sends through this one function, and none of them checked consent, so a STOP
was recorded and then ignored while the scheduler kept texting.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.gsm import to_gsm
from app.services import twilio_service

logger = logging.getLogger(__name__)


def program_number() -> str:
    """The number the assistant sends from / subscribers text to."""
    if settings.sms_provider == "signalhouse":
        return settings.signalhouse_phone_number or settings.twilio_phone_number
    return settings.twilio_phone_number


async def is_opted_out(db: AsyncSession, phone: str) -> bool:
    """True if this number has texted STOP and not re-subscribed.

    Compares on the last 10 digits: consent rows are always +E164, but callers
    pass whatever format the record they're working from happens to hold.
    """
    from app.services import consent_service

    return await consent_service.status_for(db, phone) == "opted_out"


async def send_sms(to: str, body: str, force: bool = False) -> bool:
    """Send a text, honouring opt-out. Returns True if it was sent.

    force=True is only for the carrier-mandated keyword replies (the STOP
    confirmation itself, and HELP), which must go out even to an opted-out
    number. Every other caller must respect the opt-out.
    """
    if not force:
        from app.database import get_db_session
        try:
            async with get_db_session() as db:
                if await is_opted_out(db, to):
                    logger.info("Suppressed SMS to an opted-out number")
                    return False
        except Exception as e:
            # A consent-lookup failure must not silently drop the message —
            # log loudly and send, rather than going quiet for an unknown reason.
            logger.error(f"Consent check failed, sending anyway: {e}")

    # One em dash re-encodes the whole message as UCS-2 and drops the segment
    # limit from 160 characters to 70. Doing this here rather than at each call
    # site means it covers Cord's own replies too, which are the long ones —
    # a 1,000-character research answer is seven segments in GSM and fifteen in
    # UCS-2. Letters are never transliterated; see app/services/gsm.
    body = to_gsm(body)

    if settings.sms_provider == "signalhouse":
        from app.services import signalhouse_service
        await signalhouse_service.send_sms(to=to, body=body)
    else:
        await twilio_service.send_sms(to=to, body=body)

    # Ledger last: only a message that actually went out is billable, and a
    # bookkeeping failure must never look like a send failure.
    from app.services import usage_service
    await usage_service.record_sms(to, body, outbound=True)
    return True
