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
    unicode_msg = any(ord(c) > 127 for c in body)
    single, multi = (70, 67) if unicode_msg else (160, 153)
    length = len(body)
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


async def record_sms(to_or_from: str, body: str, outbound: bool) -> None:
    segments = sms_segments(body)
    rate = settings.sms_cost_outbound if outbound else settings.sms_cost_inbound
    await record_standalone(
        "sms_out" if outbound else "sms_in",
        actor=to_or_from, quantity=segments, cost_usd=segments * rate,
        details={"segments": segments, "chars": len(body or "")},
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
    return {
        "by_type": types,
        "by_actor": [
            {"actor": a, "cost": float(c or 0), "events": int(n or 0)} for a, c, n in by_actor
        ],
        "input_tokens": int(tokens[0] or 0),
        "output_tokens": int(tokens[1] or 0),
        "total_cost": sum(v["cost"] for v in types.values()),
    }
