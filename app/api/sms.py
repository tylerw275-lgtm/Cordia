import logging

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import claude_service, twilio_service

logger = logging.getLogger(__name__)
router = APIRouter()

TWIML_EMPTY = '<?xml version="1.0"?><Response></Response>'


@router.post("/webhook/sms")
async def receive_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # Validate Twilio signature in production
    if not settings.debug:
        form_data = dict(await request.form())
        await twilio_service.verify_twilio_request(request, form_data)

    # Whitelist — only accept from Cordia's number (or test number during testcordia phase)
    allowed = {n for n in (settings.cordia_phone_number, settings.cordia_test_phone_number) if n}
    if allowed and From not in allowed:
        logger.warning(f"SMS from unknown number {From} — rejected")
        return Response(content=TWIML_EMPTY, media_type="application/xml")

    logger.info(f"Inbound SMS from {From}: {Body[:50]}...")

    try:
        conversation = await claude_service.get_or_create_conversation(db, From)
        response_text = await claude_service.chat(db, conversation.id, Body)
        await twilio_service.send_sms(to=From, body=response_text)
    except Exception as e:
        logger.error(f"Error processing SMS: {e}", exc_info=True)
        await twilio_service.send_sms(
            to=From,
            body="Something went wrong on my end. Please try again in a moment.",
        )

    return Response(content=TWIML_EMPTY, media_type="application/xml")
