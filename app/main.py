import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api import compliance, conversations, duffel_webhooks, email, family, real_estate, sms, trips
from app.config import settings
from app.middleware.logging import CorrelationIdMiddleware
from app.scheduler.scheduler import setup_scheduler, shutdown_scheduler

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.DEBUG if settings.debug else logging.INFO
    ),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Cordia starting up", model=settings.claude_model)
    setup_scheduler()
    yield
    shutdown_scheduler()
    logger.info("Cordia shut down")


app = FastAPI(
    title="Cordia AI Assistant",
    description="Personal AI assistant for Cordia Harrington",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CorrelationIdMiddleware)

app.include_router(sms.router)
app.include_router(email.router)
app.include_router(compliance.router)
app.include_router(conversations.router)
app.include_router(family.router)
app.include_router(trips.router)
app.include_router(real_estate.router)
app.include_router(duffel_webhooks.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/test-send", include_in_schema=False)
async def health_test_send(secret: str = "", to: str = "") -> dict:
    """Send a real test SMS through the active provider and report the raw
    outcome. Requires the webhook secret; only sends to the configured owner
    or test number so it can't be abused as a sending relay."""
    import httpx
    from app.services import signalhouse_service, sms_service
    from app.utils.phone import phones_match

    if not settings.signalhouse_webhook_secret or secret != settings.signalhouse_webhook_secret:
        return {"error": "bad secret"}
    target = to or settings.cordia_test_phone_number or settings.cordia_phone_number
    if not (phones_match(target, settings.cordia_phone_number)
            or phones_match(target, settings.cordia_test_phone_number)):
        return {"error": "target must be the configured owner or test number"}

    # Call the provider directly so we can surface the raw HTTP response
    payload = {
        "senderPhoneNumber": signalhouse_service._digits(settings.signalhouse_phone_number),
        "recipientPhoneNumber": signalhouse_service._digits(target),
        "messageBody": "Cord test message - if you received this, outbound SMS is working.",
    }
    url = settings.signalhouse_base_url.rstrip("/") + settings.signalhouse_send_path
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.signalhouse_api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
        return {
            "provider": settings.sms_provider,
            "url": url,
            "sent_payload_shape": {k: ("<text>" if k == "messageBody" else v) for k, v in payload.items()},
            "http_status": resp.status_code,
            "response_body": resp.text[:800],
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "url": url}


@app.get("/health/config", include_in_schema=False)
async def health_config() -> dict:
    """Deployment diagnostics: which settings are configured, never their
    values. Booleans and last-4 digits only — nothing usable by a third party."""

    def last4(v: str) -> str:
        digits = "".join(c for c in (v or "") if c.isdigit())
        return f"...{digits[-4:]}" if len(digits) >= 4 else "(not set)"

    return {
        "sms_provider": settings.sms_provider,
        "signalhouse": {
            "api_key_set": bool(settings.signalhouse_api_key),
            "webhook_secret_set": bool(settings.signalhouse_webhook_secret),
            "program_number": last4(settings.signalhouse_phone_number),
            "base_url": settings.signalhouse_base_url,
            "send_path": settings.signalhouse_send_path,
        },
        "owner": {
            "cordia_number": last4(settings.cordia_phone_number),
            "test_number": last4(settings.cordia_test_phone_number),
        },
        "anthropic_key_set": bool(settings.anthropic_api_key),
        "public_base_url": settings.public_base_url,
        "debug": settings.debug,
    }
