import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from sqlalchemy import text as sa_text

from app.api import compliance, conversations, duffel_webhooks, email, family, real_estate, sms, trips
from app.api.deps import require_admin
from app.config import settings
from app.middleware.logging import CorrelationIdMiddleware
from app.scheduler.scheduler import setup_scheduler, shutdown_scheduler

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.DEBUG if settings.debug else logging.INFO
    ),
)

logger = structlog.get_logger()


async def _bootstrap_family_data() -> None:
    """Load the family roster on boot. Never raises — a seeding problem must
    not take the assistant down, but it must be loud in the logs."""
    from sqlalchemy import func, select

    from app.data.family_seed_loader import SeedInvalid, load_seed_document
    from app.database import get_db_session
    from app.models.family import FamilyMember
    from app.services.family_seed import seed_family

    # Parse before touching the database or the lock: a bad document must seed
    # nothing rather than half a family, and must never block boot.
    try:
        seed = load_seed_document()
    except SeedInvalid as e:
        logger.error("family_seed_invalid", error=str(e))
        seed = None

    if seed is None:
        try:
            async with get_db_session() as db:
                count = await db.scalar(select(func.count()).select_from(FamilyMember))
            if count:
                logger.info("family_seed_not_configured", members_in_db=count)
            else:
                # The original bug: no roster anywhere, so Cord tells Cordia her
                # family profiles haven't been loaded.
                logger.error("family_seed_unconfigured_and_db_empty",
                             hint="set FAMILY_SEED_JSON")
        except Exception as e:
            logger.error("family_seed_status_check_failed", error=str(e))
        return

    try:
        async with get_db_session() as db:
            # try_ rather than plain pg_advisory_lock: the lock is session-scoped
            # and pooled connections outlive a failed boot, so a blocking acquire
            # could wait forever and hang startup — with no healthcheck, no SMS.
            # If another replica holds it, that replica is seeding; skip.
            got = await db.scalar(sa_text("SELECT pg_try_advisory_lock(4820193)"))
            if not got:
                logger.info("family_bootstrap_skipped", reason="another instance holds the lock")
                return
            try:
                summary = await seed_family(db, seed)
                logger.info(
                    "family_bootstrap", **{k: v for k, v in summary.items() if isinstance(v, int)}
                )
            finally:
                # Roll back first: a failed statement poisons the transaction, and
                # the unlock would raise InFailedSQLTransaction, masking the real
                # error AND leaking the lock.
                try:
                    await db.rollback()
                    await db.execute(sa_text("SELECT pg_advisory_unlock(4820193)"))
                    await db.commit()
                except Exception as unlock_error:
                    logger.error("family_bootstrap_unlock_failed", error=str(unlock_error))
    except Exception as e:
        logger.error("family_bootstrap_failed", error=str(e), error_type=type(e).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Cordia starting up", model=settings.claude_model)
    if settings.seed_family_on_startup:
        await _bootstrap_family_data()
    setup_scheduler()
    yield
    shutdown_scheduler()
    logger.info("Cordia shut down")


app = FastAPI(
    title="Cordia AI Assistant",
    description="Personal AI assistant for Cordia Harrington",
    version="1.0.0",
    lifespan=lifespan,
    # The interactive schema enumerates every route, including the admin ones.
    # There is no third-party consumer of this API — keep it off the internet.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(CorrelationIdMiddleware)

app.include_router(sms.router)
app.include_router(email.router)
app.include_router(compliance.router)
_admin = [Depends(require_admin)]
# These expose family PII, the full text of Cordia's conversations, and accept
# writes (create/patch/delete). Everything below is admin-only.
app.include_router(conversations.router, dependencies=_admin)
app.include_router(family.router, dependencies=_admin)
app.include_router(trips.router, dependencies=_admin)
app.include_router(real_estate.router, dependencies=_admin)
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
        "recipientPhoneNumber": [signalhouse_service._digits(target)],
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


@app.get("/health/test-flights", include_in_schema=False, dependencies=_admin)
async def health_test_flights(origin: str = "BNA", destination: str = "ORF") -> dict:
    """Run a real Duffel search and report what came back — confirms the token
    works and fares are reachable. Secret-gated."""
    from datetime import date, timedelta
    from app.services import duffel_service

    if not settings.duffel_access_token:
        return {"error": "DUFFEL_ACCESS_TOKEN is not set — flight search will silently return nothing"}
    depart = (date.today() + timedelta(days=30)).isoformat()
    try:
        offers = await duffel_service.search_flights(
            origin=origin.upper(), destination=destination.upper(), depart_date=depart, max_results=3
        )
        return {
            "route": f"{origin.upper()} to {destination.upper()}",
            "depart_date": depart,
            "offers_returned": len(offers),
            "sample": offers[:2],
            "verdict": "working" if offers else "token set but no offers returned - check Duffel account/live-mode",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@app.get("/health/test-email", include_in_schema=False, dependencies=_admin)
async def health_test_email() -> dict:
    """Send a real email to Cordia's configured inbox and report the outcome.
    Confirms outbound mail works end to end. Secret-gated."""
    from app.services import email_service

    if not settings.enable_email:
        return {"error": "email is disabled (ENABLE_EMAIL=false)"}
    if not settings.owner_email:
        return {"error": "OWNER_EMAIL is not set — Cord has no inbox to send reports to"}
    result = await email_service.send_email(
        to=settings.owner_email,
        subject="Cord test email",
        body_markdown=(
            "## Cord email test\n\nIf you're reading this, outbound email is working.\n\n"
            "- Long reports (flight comparisons, lease analyses, event plans) arrive this way\n"
            "- Replying to this address reaches Cord if inbound polling is on\n"
        ),
    )
    return {
        "provider": settings.email_provider,
        "to_owner_inbox": bool(settings.owner_email),
        "from": email_service.from_address(),
        "result": result,
    }


@app.get("/health/config", include_in_schema=False, dependencies=[Depends(require_admin)])
async def health_config() -> dict:
    """Deployment diagnostics: which settings are configured, never their
    values. Booleans and last-4 digits only.

    Gated on ADMIN_API_SECRET rather than the Signal House webhook secret:
    that one is shared with the SMS vendor and lives in their dashboard, so
    reusing it here would grant them deployment visibility. Different trust
    domain, different secret.
    """

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
        "email": {
            "enabled": settings.enable_email,
            "provider": settings.email_provider,
            "assistant_mailbox_set": bool(settings.email_address),
            "app_password_set": bool(settings.email_app_password),
            "resend_key_set": bool(settings.email_api_key),
            "owner_inbox_set": bool(settings.owner_email),
            "inbound_polling": bool(
                settings.enable_email and settings.email_provider == "gmail"
                and settings.email_address and settings.email_app_password
            ),
            "poll_interval_seconds": settings.email_poll_interval_seconds,
            "naples_inbox_set": bool(settings.naples_email_address and settings.naples_email_app_password),
        },
        "flights": {
            "search_enabled": settings.enable_flight_search,
            "booking_enabled": settings.enable_flight_booking,
            "duffel_token_set": bool(settings.duffel_access_token),
            "monitor_interval_minutes": settings.flight_monitor_interval_minutes,
        },
        "public_base_url": settings.public_base_url,
        "debug": settings.debug,
    }


@app.get("/health/data", include_in_schema=False, dependencies=[Depends(require_admin)])
async def health_data() -> dict:
    """Row counts for the tables Cord reads from. An empty family_members table
    is what made the assistant claim it had no family profiles — one request
    now tells you whether the data layer is actually populated."""
    from app.database import get_db_session

    from app.data.family_seed_loader import SeedInvalid, load_seed_document

    tables = ["family_members", "grandkid_activities", "family_events", "memories",
              "conversations", "messages", "contacts"]
    counts: dict = {}
    try:
        seed = load_seed_document()
        if seed is None:
            counts["family_seed_source"] = "unset"
            counts["family_seed_members"] = 0
        else:
            counts["family_seed_source"] = "env" if settings.family_seed_json.strip() else "path"
            counts["family_seed_members"] = len(seed.members)
    except SeedInvalid as e:
        counts["family_seed_source"] = "invalid"
        counts["family_seed_error"] = str(e)
    async with get_db_session() as db:
        for table in tables:
            try:
                result = await db.execute(sa_text(f"SELECT count(*) FROM {table}"))
                counts[table] = result.scalar_one()
            except Exception as e:
                counts[table] = f"error: {type(e).__name__}"
    return counts
