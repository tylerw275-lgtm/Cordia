"""What Cord costs to run, recorded as it happens.

Every rate here is a config value with a documented default, not a hardcoded
guess. Carrier and email pricing differs per account and per contract, so the
dashboard states the rates it used and where to change them — a cost report
whose assumptions are invisible is worse than no report, because it gets
believed.

Recording is strictly best-effort: a failure to write a ledger row must never
break the text or email that was actually being sent.
"""
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.usage import UsageEvent

logger = logging.getLogger(__name__)

# Anthropic list prices, USD per million tokens (input, output). Cache reads
# bill at ~0.1x input and cache writes at ~1.25x.
_MODEL_RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_DEFAULT_MODEL_RATE = (3.00, 15.00)

_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25

# Below this, a burn-rate forecast is noise dressed as a number.
_MIN_DAYS_FOR_RUNWAY = 3.0


def model_rate(model: str | None) -> tuple[float, float]:
    model = (model or "").lower()
    for prefix, rate in _MODEL_RATES.items():
        if model.startswith(prefix):
            return rate
    return _DEFAULT_MODEL_RATE


def sms_segments(body: str) -> int:
    """Carriers bill per segment, not per message.

    A 400-character text is three segments and costs three times a short one,
    so counting messages would understate a chatty month badly. Unicode (an
    emoji, a curly quote) drops the limit from 160 to 70.
    """
    if not body:
        return 1
    from app.services.gsm import is_gsm, septets

    # "Any character above ASCII" was wrong in both directions: £, é, Ö and ñ
    # are all in the GSM alphabet and cost nothing extra, so a French or Spanish
    # name was billed as though it had tripled the message.
    if is_gsm(body):
        single, multi, length = 160, 153, septets(body)
    else:
        single, multi, length = 70, 67, len(body)
    return 1 if length <= single else math.ceil(length / multi)


def ai_turn_cost(model: str, usage) -> float:
    """Cost of one Claude request from its usage block."""
    in_rate, out_rate = model_rate(model)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        inp * in_rate
        + out * out_rate
        + cache_read * in_rate * _CACHE_READ_MULTIPLIER
        + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


def server_tool_counts(usage) -> dict[str, int]:
    """Web search / fetch request counts, if the response reported any."""
    stu = getattr(usage, "server_tool_use", None)
    if stu is None:
        return {}
    counts = {}
    for field, key in (("web_search_requests", "web_search"), ("web_fetch_requests", "web_fetch")):
        n = getattr(stu, field, None)
        if n:
            counts[key] = int(n)
    return counts


async def record(
    db: AsyncSession,
    event_type: str,
    *,
    actor: str | None = None,
    quantity: int = 1,
    cost_usd: float = 0.0,
    model: str | None = None,
    usage=None,
    details: dict | None = None,
) -> None:
    """Write one ledger row. Never raises into the caller."""
    try:
        event = UsageEvent(
            event_type=event_type,
            actor=(actor or None),
            quantity=quantity,
            cost_usd=round(cost_usd, 6),
            model=model,
            details=details,
            occurred_at=datetime.now(timezone.utc),
        )
        if usage is not None:
            event.input_tokens = getattr(usage, "input_tokens", None)
            event.output_tokens = getattr(usage, "output_tokens", None)
            event.cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)
            event.cache_write_tokens = getattr(usage, "cache_creation_input_tokens", None)
        db.add(event)
        await db.commit()
    except Exception as e:
        # A ledger write must never cost us the message it was recording.
        logger.warning(f"Could not record usage event {event_type}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


async def record_standalone(event_type: str, **kw) -> None:
    """Record from a context with no session of its own (the send helpers)."""
    from app.database import get_db_session
    try:
        async with get_db_session() as db:
            await record(db, event_type, **kw)
    except Exception as e:
        logger.warning(f"Could not open a session to record {event_type}: {e}")


async def record_sms(to_or_from: str, body: str, outbound: bool, is_mms: bool = False) -> None:
    """Bill one message.

    MMS is priced per *message*, not per segment, and at roughly 7x the SMS
    rate — so a photo with a long caption costs one MMS, not five segments.
    Applying the segment maths to it would be wrong twice over.
    """
    if is_mms:
        rate = settings.mms_cost_outbound if outbound else settings.mms_cost_inbound
        await record_standalone(
            "mms_out" if outbound else "mms_in",
            actor=to_or_from, quantity=1, cost_usd=rate,
            details={"chars": len(body or "")},
        )
        return

    segments = sms_segments(body)
    rate = settings.sms_cost_outbound if outbound else settings.sms_cost_inbound
    await record_standalone(
        "sms_out" if outbound else "sms_in",
        actor=to_or_from, quantity=segments, cost_usd=segments * rate,
        details={"segments": segments, "chars": len(body or "")},
    )


def fixed_monthly_cost() -> float:
    """Charges that accrue whether or not a single message is sent."""
    return settings.monthly_number_cost + settings.monthly_campaign_cost


# Only these come out of the Signal House balance. Anthropic tokens and Resend
# email are different vendors on different bills — counting them here would make
# the remaining balance wrong, and wrong in the direction that causes a surprise
# outage.
_MESSAGING_EVENTS = ("sms_out", "sms_in", "mms_out", "mms_in")


async def credit_status(db: AsyncSession) -> dict:
    """How much prepaid Signal House credit is left, and roughly how long it lasts.

    Spend-to-date answers "what has this cost"; this answers the question that
    actually needs an action — when to top up.
    """
    row = (await db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.cost_usd), 0),
            func.min(UsageEvent.occurred_at),
        ).where(UsageEvent.event_type.in_(_MESSAGING_EVENTS))
    )).first()
    tracked = float(row[0] or 0)
    first_seen = row[1]

    spent = float(settings.signalhouse_spend_before_ledger or 0) + tracked
    remaining = float(settings.signalhouse_credit_purchased or 0) - spent

    # A runway extrapolated from a few hours of traffic is noise dressed as a
    # forecast. Withhold it until there is enough history to mean anything.
    days_of_data = 0.0
    if first_seen is not None:
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        days_of_data = (datetime.now(timezone.utc) - first_seen).total_seconds() / 86400

    daily_burn = days_remaining = None
    if days_of_data >= _MIN_DAYS_FOR_RUNWAY and tracked > 0:
        daily_burn = tracked / days_of_data
        if daily_burn > 0:
            days_remaining = int(remaining / daily_burn)

    return {
        "purchased": float(settings.signalhouse_credit_purchased or 0),
        "spent": spent,
        "remaining": remaining,
        "tracked": tracked,
        "daily_burn": daily_burn,
        "days_remaining": days_remaining,
        "days_of_data": days_of_data,
    }


# Exceptions whose message text comes from the provider rather than from
# anything the user typed. For these the detail is safe to keep, and it is the
# difference between "BadRequestError" and "tools: Tool names must be unique".
_PROVIDER_ERROR_MODULES = ("anthropic", "httpx", "sqlalchemy", "asyncpg")


def _safe_detail(exc: BaseException) -> str | None:
    """The provider's own description of a failure, if that is what this is.

    A traceback can quote the user's message straight back, and this table gets
    rendered on a web page — so the text is kept only for errors raised by a
    library, never for a generic exception whose message could be anything. The
    module the exception class came from is the test.
    """
    # An exception of ours can opt in when its message is built from provider
    # text and our own strings and can never quote the user — the same opt-in
    # shape as the tool dispatcher, and for the same reason: defaulting to
    # "include it" is how something private leaks, defaulting to "drop it" is
    # how an alert arrives with the one detail it existed to carry removed.
    if getattr(exc, "detail_is_safe", False):
        text = str(exc).strip()
        return text[:400] if text else None

    module = (type(exc).__module__ or "").split(".")[0]
    if module not in _PROVIDER_ERROR_MODULES:
        return None
    text = str(exc).strip()
    return text[:400] if text else None


async def record_error(
    where: str,
    exc: BaseException,
    actor: str | None = None,
    message: str | None = None,
) -> None:
    """Record that something broke, and tell the operator.

    Always stores the exception type and where it happened. Stores the message
    too when it came from a library rather than from user input — diagnosing the
    same failure twice by asking someone to go and read a log is the thing this
    is meant to avoid.

    Every error in the system already funnels through here, so this is also
    where the operator alert belongs: sms_reply, tool failures and inbound email
    are covered by writing it once, and so is whatever gets added next.
    `message` is the text that triggered the failure — passed to the alert,
    never into the stored row, which is rendered on a web page.
    """
    details = {"where": where, "error_type": type(exc).__name__}
    detail = _safe_detail(exc)
    if detail:
        details["detail"] = detail
    await record_standalone("error", actor=actor, cost_usd=0.0, details=details)

    # After the ledger write, never before: an alert must not be able to cost us
    # the record of what happened. alerts.notify_error swallows its own failures.
    from app.services import alerts
    await alerts.notify_error(
        where, type(exc).__name__, detail=detail, actor=actor, message=message,
    )


async def record_email(address: str, outbound: bool) -> None:
    rate = settings.email_cost_outbound if outbound else settings.email_cost_inbound
    await record_standalone(
        "email_out" if outbound else "email_in", actor=address, cost_usd=rate,
    )


# --- reporting -------------------------------------------------------------

async def summary(db: AsyncSession, since: datetime | None = None) -> dict:
    """Totals by event type, plus a per-person breakdown."""
    where = (UsageEvent.occurred_at >= since,) if since else ()

    by_type = (await db.execute(
        select(
            UsageEvent.event_type,
            func.sum(UsageEvent.quantity),
            func.sum(UsageEvent.cost_usd),
        ).where(*where).group_by(UsageEvent.event_type)
    )).all()

    by_actor = (await db.execute(
        select(
            UsageEvent.actor,
            func.sum(UsageEvent.cost_usd),
            func.count(UsageEvent.id),
        )
        .where(*where)
        .where(UsageEvent.actor.isnot(None))
        .group_by(UsageEvent.actor)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
    )).all()

    tokens = (await db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        ).where(*where)
    )).first()

    types = {t: {"quantity": int(q or 0), "cost": float(c or 0)} for t, q, c in by_type}
    # Errors are recorded in the same ledger for one place to look, but they are
    # events, not spend.
    usage_cost = sum(v["cost"] for k, v in types.items() if k != "error")
    # The number renewal and campaign fee are charged once a month regardless of
    # traffic. `since` is only ever the start of a month, so a period query is a
    # single month's worth; an all-time query has no meaningful fixed component
    # to state, so it reports usage only and says so on the card.
    fixed = fixed_monthly_cost() if since else 0.0
    return {
        "by_type": types,
        "by_actor": [
            {"actor": a, "cost": float(c or 0), "events": int(n or 0)} for a, c, n in by_actor
        ],
        "input_tokens": int(tokens[0] or 0),
        "output_tokens": int(tokens[1] or 0),
        "usage_cost": usage_cost,
        "fixed_cost": fixed,
        "total_cost": usage_cost + fixed,
    }


async def recent_errors(db: AsyncSession, limit: int = 10) -> list[dict]:
    """The last few failures, newest first, for the dashboard."""
    rows = (await db.execute(
        select(UsageEvent)
        .where(UsageEvent.event_type == "error")
        .order_by(UsageEvent.occurred_at.desc())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "when": r.occurred_at,
            "where": (r.details or {}).get("where", "unknown"),
            "error_type": (r.details or {}).get("error_type", "unknown"),
            "detail": (r.details or {}).get("detail"),
            "actor": r.actor,
        }
        for r in rows
    ]
