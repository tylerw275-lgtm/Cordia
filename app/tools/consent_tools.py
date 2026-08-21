"""Cordia's approval controls for the public consent form.

The form is linked from a public website, so anyone who finds the link can sign
it. Signing records consent — the legal evidence, which is never edited away —
but it grants no access. These tools are how Cordia makes the access decision
from her phone, in the same conversation where Cord told her someone signed.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import consent_service
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


TOOL_SCHEMAS = [
    {
        "name": "list_consent_requests",
        "description": (
            "List everyone who signed the consent form and is waiting on Cordia's "
            "approval before they can message the assistant. Shows the name they "
            "typed and their number. Use whenever she asks who's waiting, who "
            "signed up, or why someone can't reach you."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "approve_consent_request",
        "description": (
            "Let a number that signed the consent form start messaging the "
            "assistant. ONLY call this when Cordia has clearly said to approve "
            "that specific person — never infer it, and never approve on her "
            "behalf. She can identify them by name or by the last four digits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The number to approve. Last 4 digits are enough if it matches exactly one pending request."},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "reject_consent_request",
        "description": (
            "Deny a number access to the assistant. Their consent record is kept "
            "(it is required compliance evidence) but they can never message the "
            "assistant. Use when Cordia doesn't recognise a number, or says to "
            "block or reject it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The number to reject. Last 4 digits are enough if it matches exactly one pending request."},
            },
            "required": ["phone"],
        },
    },
]


def _pretty(raw: str) -> str:
    d = normalize_phone(raw)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (raw or "")


async def _resolve_target(db: AsyncSession, given: str) -> tuple[str | None, dict | None]:
    """Accept a full number or just the last four digits.

    Four digits are how a person actually replies to the alert text, but they
    are ambiguous in principle — so they only resolve when exactly one pending
    request ends in them. Anything else comes back as a question, never a guess
    at which stranger to let in.
    """
    digits = "".join(c for c in (given or "") if c.isdigit())
    if len(digits) >= 10:
        return normalize_phone(digits), None
    if len(digits) == 4:
        matches = [p for p in await consent_service.list_pending(db)
                   if normalize_phone(p["phone"]).endswith(digits)]
        if len(matches) == 1:
            return normalize_phone(matches[0]["phone"]), None
        if not matches:
            return None, {"ok": False, "reason": "no_pending_match",
                          "message": (f"No one waiting for approval has a number ending in "
                                      f"{digits}. Show Cordia the pending list instead.")}
        return None, {"ok": False, "reason": "ambiguous",
                      "message": (f"{len(matches)} pending requests end in {digits}. Ask Cordia "
                                  "which one, using the names, before doing anything.")}
    return None, {"ok": False, "reason": "unusable_number",
                  "message": "That doesn't look like a phone number. Ask Cordia for the full number or the last four digits."}


async def list_consent_requests_handler(db: AsyncSession, **kw) -> dict:
    pending = await consent_service.list_pending(db)
    return {
        "pending_count": len(pending),
        "pending": [
            {"name": p["name"] or "(no name given)", "phone": _pretty(p["phone"]),
             "last4": normalize_phone(p["phone"])[-4:], "signed": p["signed_at"]}
            for p in pending
        ],
        "message": (
            "Nobody is waiting for approval right now."
            if not pending else
            "These people signed the consent form but cannot message the assistant until "
            "Cordia approves them. Read her the names and numbers and ask which to let in. "
            "Do not approve anyone she hasn't named."
        ),
    }


async def _decide(db: AsyncSession, given: str, status: str) -> dict:
    target, problem = await _resolve_target(db, given)
    if problem:
        return problem
    changed = await consent_service.set_status(db, target, status)
    if not changed:
        return {"ok": False, "reason": "no_consent_record",
                "message": (f"There's no consent record for {_pretty(target)}, so there's "
                            "nothing to approve or reject. They haven't signed the form.")}
    if status == "approved":
        msg = (f"{_pretty(target)} is approved and can now message the assistant. "
               "Confirm that to Cordia in one short line.")
    else:
        msg = (f"{_pretty(target)} is rejected and can never message the assistant. "
               "Their consent record is kept as required compliance evidence, but it "
               "grants them nothing. Confirm that to Cordia in one short line.")
    return {"ok": True, "phone": _pretty(target), "approval_status": status, "message": msg}


async def approve_consent_request_handler(db: AsyncSession, **kw) -> dict:
    return await _decide(db, kw.get("phone", ""), "approved")


async def reject_consent_request_handler(db: AsyncSession, **kw) -> dict:
    return await _decide(db, kw.get("phone", ""), "rejected")


HANDLERS = {
    "list_consent_requests": list_consent_requests_handler,
    "approve_consent_request": approve_consent_request_handler,
    "reject_consent_request": reject_consent_request_handler,
}
