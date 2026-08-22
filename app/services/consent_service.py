"""Consent approval.

The consent form is linked from a public website, so anyone can submit it.
Signing it creates the legal consent record — that part is never altered or
deleted, because it is the compliance evidence. What it does NOT do is grant
access: a submission starts as *pending* and Cordia decides whether that
person may exchange messages with her assistant.

Approval is therefore an access control layered on top of consent, not a
replacement for it. Someone can be validly consented and still be denied.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

_MATCH = "right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) = :digits"


async def is_approved(db: AsyncSession, phone: str) -> bool:
    """True only if this number consented, has not opted out, and Cordia
    approved it. Every access path should ask this, not just 'did they sign'."""
    digits = normalize_phone(phone)
    if not digits:
        return False
    row = (await db.execute(
        text(f"SELECT 1 FROM sms_consent WHERE {_MATCH} AND consented_at IS NOT NULL "
             "AND opted_out_at IS NULL AND approval_status = 'approved'"),
        {"digits": digits},
    )).first()
    return row is not None


async def status_for(db: AsyncSession, phone: str) -> str:
    """'approved' | 'pending' | 'rejected' | 'opted_out' | 'no_consent'."""
    digits = normalize_phone(phone)
    if not digits:
        return "no_consent"
    row = (await db.execute(
        text(f"SELECT approval_status, opted_out_at IS NOT NULL AS opted_out "
             f"FROM sms_consent WHERE {_MATCH} AND consented_at IS NOT NULL"),
        {"digits": digits},
    )).first()
    if row is None:
        return "no_consent"
    if row.opted_out:
        return "opted_out"
    return row.approval_status or "pending"


async def status_by_phone(db: AsyncSession) -> dict[str, str]:
    """Every number's status at once, keyed by its last 10 digits.

    The bulk form of `status_for`, for callers listing a roster. It exists so
    that a listing and a send gate cannot disagree: `list_sms_roster` used to
    write its own query, left out approval_status, and announced people as
    consented that `ask_family_member` then refused to text.
    """
    rows = (await db.execute(text(
        "SELECT phone, consented_at, opted_out_at, approval_status FROM sms_consent"
    ))).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        digits = normalize_phone(row.phone)
        if not digits or row.consented_at is None:
            continue
        if row.opted_out_at is not None:
            out[digits] = "opted_out"
        else:
            out[digits] = row.approval_status or "pending"
    return out


async def list_pending(db: AsyncSession) -> list[dict]:
    """Everyone waiting on Cordia's decision, with the name they typed on the
    form so she can recognise them."""
    rows = (await db.execute(text("""
        SELECT c.phone, c.consented_at,
               (SELECT full_name FROM consent_submissions s
                WHERE right(regexp_replace(s.phone,'[^0-9]','','g'),10)
                    = right(regexp_replace(c.phone,'[^0-9]','','g'),10)
                ORDER BY s.submitted_at DESC LIMIT 1) AS submitted_name
        FROM sms_consent c
        WHERE c.approval_status = 'pending' AND c.consented_at IS NOT NULL
          AND c.opted_out_at IS NULL
        ORDER BY c.consented_at DESC
    """))).fetchall()
    return [
        {"phone": r.phone, "name": r.submitted_name,
         "signed_at": r.consented_at.isoformat() if r.consented_at else None}
        for r in rows
    ]


async def set_status(db: AsyncSession, phone: str, status: str) -> bool:
    if status not in ("approved", "rejected", "pending"):
        raise ValueError(f"unknown approval status: {status}")
    digits = normalize_phone(phone)
    if not digits:
        return False
    result = await db.execute(
        text(f"UPDATE sms_consent SET approval_status = :status, reviewed_at = :ts "
             f"WHERE {_MATCH}"),
        {"status": status, "ts": datetime.now(timezone.utc), "digits": digits},
    )
    await db.commit()
    changed = (result.rowcount or 0) > 0
    if changed:
        logger.info(f"Consent for ...{digits[-4:]} set to {status}")
    return changed


async def notify_owner_of_submission(name: str, phone: str) -> None:
    """Tell Cordia someone signed, so approval is a decision she makes rather
    than a thing that happens silently. Never raises into the form handler —
    a failed notification must not break someone's consent submission."""
    from app.config import settings
    from app.services import sms_service

    if not settings.cordia_phone_number:
        return
    digits = normalize_phone(phone)
    pretty = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else phone
    try:
        await sms_service.send_sms(
            to=settings.cordia_phone_number,
            body=(f"{name or 'Someone'} at {pretty} just signed the consent form to text "
                  f"with me. They can't reach me until you approve. Reply "
                  f"'approve {digits[-4:]}' to let them in, or 'reject {digits[-4:]}' "
                  f"if you don't recognise them."),
        )
    except Exception as e:
        logger.error(f"Could not notify Cordia of a consent submission: {e}")
