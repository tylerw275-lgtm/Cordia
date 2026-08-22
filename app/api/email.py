"""Inbound email webhook (for the Resend provider). For the Gmail provider,
the IMAP poller calls the same processing logic instead.
"""
import base64
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services import email_inbound, email_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Generous enough for a forwarded document, small enough that one message
# cannot dominate a turn.
_MAX_BODY_CHARS = 50_000


# Resend signs webhooks with the Svix scheme: the signed payload is
# "{id}.{timestamp}.{body}", HMAC-SHA256 with the base64 secret that follows
# the "whsec_" prefix, and the header may carry several space-separated
# versioned signatures.
_SIGNATURE_TOLERANCE_SECONDS = 5 * 60


def _verify_svix_signature(secret: str, headers, raw_body: bytes) -> bool:
    msg_id = headers.get("svix-id") or headers.get("webhook-id") or ""
    timestamp = headers.get("svix-timestamp") or headers.get("webhook-timestamp") or ""
    signature_header = headers.get("svix-signature") or headers.get("webhook-signature") or ""
    if not (msg_id and timestamp and signature_header):
        return False

    # Reject replays of an old, validly-signed delivery.
    try:
        if abs(time.time() - int(timestamp)) > _SIGNATURE_TOLERANCE_SECONDS:
            logger.warning("Inbound email rejected — signature timestamp outside tolerance")
            return False
    except ValueError:
        return False

    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:
        logger.error("EMAIL_WEBHOOK_SIGNING_SECRET is not a valid Svix secret")
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        _, _, candidate = part.partition(",")
        if candidate and hmac.compare_digest(candidate, expected):
            return True
    return False


def _first(payload: dict, *keys) -> str:
    for k in keys:
        if payload.get(k):
            return str(payload[k])
    return ""


async def _parse_inbound(request: Request, raw_body: bytes) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        import json
        try:
            data = json.loads(raw_body or b"{}")
        except ValueError:
            return {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = {**data, **data["data"]}
        return data if isinstance(data, dict) else {}
    return dict(await request.form())


@router.post("/webhook/email")
async def receive_email(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    # Fail closed: an unset secret used to mean "no authentication at all", so
    # anyone could POST a forged sender and drive a model turn. Prefer Resend's
    # signature when configured; the URL secret is the fallback.
    raw_body = await request.body()
    authorized = False
    if settings.email_webhook_signing_secret:
        reason = "signature_invalid"
        authorized = _verify_svix_signature(
            settings.email_webhook_signing_secret, request.headers, raw_body
        )
    elif settings.email_inbound_secret:
        reason = "url_secret_invalid"
        supplied = request.query_params.get("secret") or request.headers.get("X-Webhook-Secret") or ""
        authorized = hmac.compare_digest(
            supplied.encode("utf-8"), settings.email_inbound_secret.encode("utf-8")
        )
    else:
        # Neither secret configured. Failing closed is right, but silently
        # failing closed means outbound email keeps working while every reply
        # is refused — which reads as "it emails me but never answers me".
        reason = "no_secret_configured"
    if not authorized:
        logger.warning(f"Inbound email rejected ({reason})")
        # Before the body is parsed, so none of the other reporting can see it.
        try:
            from app.services import alerts
            await alerts.notify_inbound_webhook_rejected(reason)
        except Exception as e:                   # pragma: no cover - defensive
            logger.error(f"Could not report a rejected inbound webhook: {e}")
        return Response(status_code=403)

    payload = await _parse_inbound(request, raw_body)

    # Resend posts every event type to the same endpoint — email.sent,
    # email.delivered, email.bounced, email.received. Only the last one is a
    # message TO Cord; treating a delivery receipt as inbound mail would feed
    # our own outgoing subject line back into a model turn.
    event_type = str(payload.get("type") or "")
    if event_type and event_type != "email.received":
        return Response(status_code=200)

    sender = email_inbound.extract_email(payload.get("from") or payload.get("sender") or payload.get("From"))
    subject = _first(payload, "subject", "Subject")
    body = _first(payload, "text", "plain", "body-plain", "stripped-text", "TextBody", "body")

    # Resend's email.received carries metadata ONLY — no body, by design (so a
    # large attachment can't blow past a serverless request limit). Without this
    # fetch the body is always "", process_inbound_email returns
    # "ignored_empty_body", and every reply is dropped while the webhook reports
    # a clean 200. Other providers post the body inline, so only fetch when it
    # is actually missing.
    email_id = _first(payload, "email_id", "id")
    if not body and email_id:
        try:
            fetched = await email_service.fetch_received_email(email_id)
        except email_service.ReceivedEmailUnavailable as e:
            # 500, not 200: this message is real and retryable. Acknowledging it
            # would discard someone's email permanently.
            logger.error(f"Inbound email {email_id} metadata arrived but content did not: {e}")
            # The last step in this path that reported nothing. Resend's retries
            # exhaust, outbound keeps working, and the symptom is identical to
            # every other inbound failure — so it goes through the same reporting
            # as everything else, carrying the status and the URL attempted.
            try:
                from app.services import usage_service
                await usage_service.record_error(
                    "email_inbound_fetch", e, actor=sender or email_id,
                    message=f"subject: {subject}" if subject else None,
                )
            except Exception as report_error:    # pragma: no cover - defensive
                logger.error(f"Could not report a failed content fetch: {report_error}")
            return JSONResponse(status_code=500, content={"status": "content_fetch_failed"})
        # Cap the fetched body: an inbound message is attacker-influenced input
        # and this is the one path where its full length reaches a model turn.
        body = (fetched.get("text") or email_inbound.html_to_text(fetched.get("html") or ""))[:_MAX_BODY_CHARS]
        sender = sender or email_inbound.extract_email(fetched.get("from"))
        subject = subject or str(fetched.get("subject") or "")

    # The status is echoed back in the 200 body so it appears in the provider's
    # webhook log — "ignored_unknown_sender" there is far easier to act on than
    # silence plus a server log nobody is watching.
    try:
        status = await email_inbound.process_inbound_email(db, sender, subject, body)
    except Exception as e:
        logger.error(f"Error processing inbound email: {e}", exc_info=True)
        status = f"error:{type(e).__name__}"
        # Only logged until now — and inbound email is precisely the path that
        # was broken for days without anyone noticing.
        from app.services import usage_service
        await usage_service.record_error(
            "email_inbound", e, actor=sender, message=body,
        )

    return JSONResponse(status_code=200, content={"status": status})
