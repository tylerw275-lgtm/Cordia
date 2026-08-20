"""Provider-agnostic SMS sending.

All outbound texting in the app goes through send_sms() here; the active
provider is chosen by settings.sms_provider ("twilio" or "signalhouse").
Message content and STOP/START/HELP keyword handling live above this layer.

Opt-out enforcement lives *here*, deliberately: every proactive job and webhook
sends through this one function, and none of them checked consent, so a STOP
was recorded and then ignored while the scheduler kept texting.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import twilio_service
from app.utils.phone import normalize_phone

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
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    result = await db.execute(
        text(
            "SELECT 1 FROM sms_consent "
            "WHERE right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) = :digits "
            "AND opted_out_at IS NOT NULL"
        ),
        {"digits": normalized},
    )
    return result.first() is not None


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

    if settings.sms_provider == "signalhouse":
        from app.services import signalhouse_service
        await signalhouse_service.send_sms(to=to, body=body)
    else:
        await twilio_service.send_sms(to=to, body=body)
    return True
