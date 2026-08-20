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
