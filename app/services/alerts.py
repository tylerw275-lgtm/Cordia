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

    return await _throttled(
        (where, error_type),
        lambda suppressed, first_seen: _error_body(
            where, error_type, detail, actor, message, suppressed, first_seen,
        ),
        subject=f"[Cordia] Error in {where}: {error_type}",
    )


async def _throttled(key: tuple, build_body, subject: str) -> bool:
    """Send one alert, unless we have said this recently or said too much.

    `build_body(suppressed, first_seen)` is called only if the alert is actually
    going out, so the count of what was held back can be folded into the text.
    """
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
        logger.warning(f"Operator alert cap reached; suppressing {key}")
        return False

    ok = await _send(subject, build_body(suppressed, first_seen))
    _SEEN[key] = {
        # Only a delivered alert starts the next quiet period.
        "last_sent": now if ok else last_sent,
        "suppressed": 0 if ok else suppressed + 1,
        "since": now if ok else first_seen,
    }
    _trim()
    return ok


def _also_line(suppressed: int, first_seen: datetime) -> str:
    if not suppressed:
        return ""
    return (f"\n**Also:** {suppressed} more of these since "
            f"{first_seen.isoformat(timespec='minutes')}, not emailed separately.")


def _error_body(where, error_type, detail, actor, message, suppressed, first_seen) -> str:
    lines = [
        f"**Where:** `{where}`",
        f"**Error:** `{error_type}`",
        f"**Who:** {_mask(actor)}",
        f"**When:** {_now().isoformat(timespec='seconds')}",
    ]
    if detail:
        lines.append(f"**Provider said:** {detail}")
    body = "## Cord hit an error\n\n" + "\n".join(lines) + _also_line(suppressed, first_seen)
    if message:
        body += f"\n\n### What she sent\n\n> {message.strip()[:2000]}\n"
    return body + (
        f"\n\n---\n\nShe was told: \"Something went wrong on my end. Please try "
        f"again in a moment.\"\n\n"
        f"Full ledger: {settings.public_base_url.rstrip('/')}/health/dashboard"
    )


def _trim() -> None:
    while len(_SEEN) > _MAX_TRACKED:
        _SEEN.popitem(last=False)


# Why an inbound email produced no reply, in words that name the fix rather than
# the symptom. Every one of these used to be a log line and a 200, which is how
# "it won't answer my emails" reached the operator twice with nothing to go on.
_DROP_REASONS = {
    "ignored_unknown_sender": (
        "That address is not on the roster, so Cord ignored it on purpose — it will "
        "not converse with an address it does not recognise. If this is Cordia, set "
        "OWNER_EMAIL to it, or add the address to her principal record. If it is a "
        "relative, they need circle access and an approved consent record."
    ),
    "ignored_unapproved_sender": (
        "On the roster but not approved (or rejected) by Cordia, so it was ignored "
        "without a reply. Approve them in the dashboard if that is wrong."
    ),
    "ignored_empty_body": (
        "The message arrived with no readable text. Usually the provider sent "
        "metadata only and the body fetch failed, or the mail was HTML that "
        "flattened to nothing."
    ),
    "ignored_no_sender": (
        "The provider delivered a message with no From address, which normally "
        "means the webhook payload was not the shape we expect."
    ),
}


async def notify_inbound_email_dropped(
    reason: str, sender: str | None, subject: str | None = None
) -> bool:
    """Someone emailed the assistant and got no answer.

    A silent 200 is right for the sender — replying would confirm the address to
    whoever is probing it — but wrong for the operator, who otherwise learns
    about it only when the person complains.
    """
    if _SENDING:
        return False

    def body(suppressed: int, first_seen) -> str:
        text = (
            "## An email got no reply\n\n"
            f"**From:** {_mask(sender)}\n"
            f"**Subject:** {(subject or '(none)')[:120]}\n"
            f"**Reason:** `{reason}`\n"
            f"**When:** {_now().isoformat(timespec='seconds')}\n"
            + _also_line(suppressed, first_seen)
        )
        explain = _DROP_REASONS.get(reason)
        if explain:
            text += f"\n\n### What that means\n\n{explain}\n"
        return text + (
            f"\n\n---\n\nNothing was sent back to them, deliberately: replying would "
            f"confirm the address to whoever sent it.\n\n"
            f"Config: {settings.public_base_url.rstrip('/')}/health/config"
        )

    return await _throttled(
        ("email_inbound", reason), body,
        subject=f"[Cordia] Inbound email ignored: {reason}",
    )


_WEBHOOK_REJECTIONS = {
    "no_secret_configured": (
        "Neither EMAIL_WEBHOOK_SIGNING_SECRET nor EMAIL_INBOUND_SECRET is set, so the "
        "endpoint refuses everything — it fails closed on purpose, because an unset "
        "secret used to mean anyone could POST a forged sender and drive a model turn.\n\n"
        "**Every inbound email is being rejected right now.** Outbound still works, which "
        "is why this looks like \"it emails me but never replies\".\n\n"
        "Fix: copy the signing secret (`whsec_...`) from the Resend dashboard's webhook "
        "settings into EMAIL_WEBHOOK_SIGNING_SECRET in Railway."
    ),
    "signature_invalid": (
        "EMAIL_WEBHOOK_SIGNING_SECRET is set but the signature did not verify. Either the "
        "secret in Railway no longer matches the one in the Resend dashboard, or something "
        "other than Resend is posting to the endpoint."
    ),
    "url_secret_invalid": (
        "EMAIL_INBOUND_SECRET is set but the value in the request did not match. Check the "
        "?secret= on the webhook URL configured at the provider."
    ),
}


async def notify_inbound_webhook_rejected(reason: str) -> bool:
    """The inbound email endpoint turned a delivery away.

    Worth its own alert rather than folding into the dropped-email one, because
    this happens *before* the message is parsed: no sender, no subject, and none
    of the other reporting sees it. It is also the single most likely cause of
    inbound email appearing to do nothing at all.
    """
    if _SENDING:
        return False

    def body(suppressed: int, first_seen) -> str:
        text = (
            "## Inbound email is being refused at the door\n\n"
            f"**Reason:** `{reason}`\n"
            f"**When:** {_now().isoformat(timespec='seconds')}\n"
            + _also_line(suppressed, first_seen)
        )
        explain = _WEBHOOK_REJECTIONS.get(reason)
        if explain:
            text += f"\n\n{explain}\n"
        return text + (
            f"\n\n---\n\nThe provider is being told 403, so it will retry and then give up. "
            f"Check its delivery log for failed attempts.\n\n"
            f"Config: {settings.public_base_url.rstrip('/')}/health/config"
        )

    return await _throttled(
        ("email_webhook", reason), body,
        subject=f"[Cordia] Inbound email webhook rejected: {reason}",
    )
