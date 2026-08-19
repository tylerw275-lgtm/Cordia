"""Provider-agnostic SMS sending.

All outbound texting in the app goes through send_sms() here; the active
provider is chosen by settings.sms_provider ("twilio" or "signalhouse").
Consent rules, message content, and STOP/START/HELP handling live above this
layer and are identical for every provider.
"""
from app.config import settings
from app.services import twilio_service


def program_number() -> str:
    """The number the assistant sends from / subscribers text to."""
    if settings.sms_provider == "signalhouse":
        return settings.signalhouse_phone_number or settings.twilio_phone_number
    return settings.twilio_phone_number


async def send_sms(to: str, body: str) -> None:
    if settings.sms_provider == "signalhouse":
        from app.services import signalhouse_service
        await signalhouse_service.send_sms(to=to, body=body)
    else:
        await twilio_service.send_sms(to=to, body=body)
