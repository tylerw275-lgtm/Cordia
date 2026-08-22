"""Telling the people who run the service when something needs them.

Two things used to end at the database. A feature request became a
`feature_request` memory nobody reads, and a failure became a row in the usage
ledger on a dashboard nobody is watching at three in the morning. Both are now
emailed to the operator.

Three properties matter more than the sending itself.

**It never raises.** `record_error` is called from inside `except` blocks that
are already handling a failure. An alert that throws there turns a bad reply
into no reply at all, which is a far worse trade than a missed email.

**It never storms.** An Anthropic outage means every message Cordia sends
produces the same error. The first is worth an email; the next forty are worth a
number. Repeats of the same `(where, error_type)` inside a cooldown are counted
and folded into the following send, and an hourly cap bounds the worst case
whatever happens.

**It never recurses.** The alert path can itself fail — the mail provider is
exactly the sort of thing that goes down — and an alert about a failed alert
would loop.

State is in-process, which is sound because `main.py` refuses to boot with more
than one worker.
"""
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)

# (where, error_type) -> {"last_sent": datetime, "suppressed": int, "since": datetime}
_SEEN: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
_MAX_TRACKED = 200

# Send times inside the current hour, for the global cap.
_SENT_AT: list[datetime] = []

# Set while an alert is being sent, so a failure inside the alert path cannot
# trigger an alert about itself.
_SENDING = False


def reset() -> None:
    """Clear the throttle state. For tests, and for nothing else."""
    _SEEN.clear()
    _SENT_AT.clear()
    global _SENDING
    _SENDING = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _under_hourly_cap() -> bool:
    cutoff = _now() - timedelta(hours=1)
    _SENT_AT[:] = [t for t in _SENT_AT if t > cutoff]
    return len(_SENT_AT) < settings.alert_max_per_hour


def _mask(value: str | None) -> str:
    """Enough to tell two people apart, not enough to be a contact list."""
    if not value:
        return "(unknown)"
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = "".join(c for c in value if c.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "(unknown)"


async def _send(subject: str, body: str) -> bool:
    """Deliver one alert. Swallows everything; returns whether it went."""
    global _SENDING
    to = settings.operator_email
    if not to:
        return False
    if _SENDING:
        # Reached from inside an alert. Log and stop, or this loops.
        logger.error(f"Suppressed a nested operator alert: {subject}")
        return False

    _SENDING = True
    try:
        from app.services import email_service
        result = await email_service.send_email(to=to, subject=subject, body_markdown=body)
        sent = bool(result.get("sent"))
        if not sent:
            logger.warning(f"Operator alert not sent ({result.get('reason')}): {subject}")
        else:
            _SENT_AT.append(_now())
        return sent
    except Exception as e:
        # Never propagate: the caller is usually already handling a failure.
        logger.error(f"Could not send operator alert '{subject}': {e}")
        return False
    finally:
        _SENDING = False


async def notify_feature_request(
    title: str, details: str, priority: str = "nice_to_have", actor: str | None = None
) -> bool:
    """Every one of these is emailed, unthrottled.

    They are rare and each is the entire point — a request Cordia made that the
    software cannot do yet. Her own words go in verbatim: paraphrasing a feature
    request is how it gets built wrong.
    """
    if _SENDING:
        return False
    body = (
        f"## {title}\n\n"
        f"**Priority (as Cord read it):** {priority}\n"
        f"**From:** {_mask(actor)}\n"
        f"**When:** {_now().isoformat(timespec='seconds')}\n\n"
        f"### In her words\n\n{details}\n\n"
        f"---\n\nLogged to the feature_request backlog. "
        f"Ask Cord to `list_feature_requests` to see them all."
    )
    return await _send(f"[Cordia] Feature request: {title[:80]}", body)


async def notify_error(
    where: str,
    error_type: str,
    detail: str | None = None,
    actor: str | None = None,
    message: str | None = None,
) -> bool:
    """Alert on a failure, deduplicated by where it happened and what broke.

    `message` is the text that triggered it. It is included deliberately: this
    is an operator's inbox, not the error table rendered on a web page that
    `usage_service._safe_detail` is written for, and without it the alert says
    something broke while leaving no way to reproduce it. It does mean Cordia's
    words reach the operator's mailbox.
    """
    if _SENDING:
        return False

    key = (where, error_type)
    now = _now()
    seen = _SEEN.get(key)
    _SEEN.pop(key, None)

    cooldown = timedelta(minutes=settings.alert_cooldown_minutes)
    suppressed = 0
    first_seen = now
    # `last_sent` is None until one actually goes out. A failed send must not
    # start the quiet period, or a single SMTP blip silences the whole incident
    # for half an hour — the opposite of what an alert is for.
    last_sent = None
    if seen is not None:
        first_seen = seen.get("since", now)
        last_sent = seen.get("last_sent")
        suppressed = seen.get("suppressed", 0)
        if last_sent is not None and now - last_sent < cooldown:
            # Inside the quiet period: count it and say nothing.
            seen["suppressed"] = suppressed + 1
            _SEEN[key] = seen
            _trim()
            return False

    if not _under_hourly_cap():
        _SEEN[key] = {"last_sent": last_sent, "suppressed": suppressed + 1,
                      "since": first_seen}
        _trim()
        logger.warning(f"Operator alert cap reached; suppressing {where}/{error_type}")
        return False

    lines = [
        f"**Where:** `{where}`",
        f"**Error:** `{error_type}`",
        f"**Who:** {_mask(actor)}",
        f"**When:** {now.isoformat(timespec='seconds')}",
    ]
    if detail:
        lines.append(f"**Provider said:** {detail}")
    if suppressed:
        lines.append(
            f"**Also:** {suppressed} more of these since "
            f"{first_seen.isoformat(timespec='minutes')}, not emailed separately."
        )
    body = "## Cord hit an error\n\n" + "\n".join(lines)
    if message:
        body += f"\n\n### What she sent\n\n> {message.strip()[:2000]}\n"
    body += (
        f"\n\n---\n\nShe was told: \"Something went wrong on my end. Please try "
        f"again in a moment.\"\n\n"
        f"Full ledger: {settings.public_base_url.rstrip('/')}/health/dashboard"
    )

    ok = await _send(f"[Cordia] Error in {where}: {error_type}", body)
    _SEEN[key] = {
        # Only a delivered alert starts the next quiet period.
        "last_sent": now if ok else last_sent,
        "suppressed": 0 if ok else suppressed + 1,
        "since": now if ok else first_seen,
    }
    _trim()
    return ok


def _trim() -> None:
    while len(_SEEN) > _MAX_TRACKED:
        _SEEN.popitem(last=False)
