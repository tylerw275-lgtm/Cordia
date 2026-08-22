"""Who is this, and may they be here.

One decision, asked the same way whatever channel a message arrived on.

It used to be two functions. `sms.py` blocked pending and rejected numbers;
`email_inbound.py` checked `has_circle_access` and never consulted
`consent_service` at all — so rejecting somebody in the dashboard stopped them
texting and did nothing whatsoever about email. They were brought into agreement
one gate at a time, which is exactly the arrangement that lets the next gate be
added to one channel and forgotten on the other.

`consent_service.is_approved` says in its own docstring that every access path
should ask it. This is that path.
"""
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import consent_service, family_circle_service, principal_service
from app.utils.email_address import emails_match
from app.utils.phone import phones_match

logger = logging.getLogger(__name__)

# owner            — a principal with their own full assistant
# family           — a family-circle member, restricted prompt and toolset
# opted_out        — texted STOP; no turn runs and nothing is billed
# unapproved       — on a profile, but Cordia has not approved (or has rejected)
# unknown          — nobody we recognise
ROLES = ("owner", "family", "opted_out", "unapproved", "unknown")


@dataclass(frozen=True)
class Sender:
    """The identity behind an inbound message.

    `principal` and `member` are kept apart on purpose: a principal gets the
    full prompt and toolset, a family member gets a much smaller one, and
    conflating them would hand out the wrong one.
    """
    role: str
    principal: object | None = None      # AuthorizedUser
    member: object | None = None         # FamilyMember

    @property
    def name(self) -> str:
        who = self.principal or self.member
        return getattr(who, "name", None) or "unknown"

    @property
    def is_allowed(self) -> bool:
        return self.role in ("owner", "family")


async def resolve(db: AsyncSession, *, phone: str = "", email: str = "") -> Sender:
    """Classify an inbound sender by whichever identifier the channel has.

    Consent is keyed by phone, so an email-only sender is checked against the
    number on their profile. Someone Cordia added with only an email has no
    consent row, and that is not the same as being rejected — they reached the
    roster because she put them there.
    """
    principal = None
    if phone:
        principal = await principal_service.resolve_by_phone(db, phone)
    if principal is None and email:
        principal = await principal_service.resolve_by_email(db, email)

    gate_phone = phone or getattr(principal, "phone", "") or ""
    status = await consent_service.status_for(db, gate_phone) if gate_phone else "no_consent"

    if principal is not None:
        return Sender("opted_out" if status == "opted_out" else "owner", principal=principal)

    # Config fallback so a deployment that has not set PRINCIPALS_JSON still
    # resolves Cordia rather than rejecting her own number or address.
    if phone and (phones_match(phone, settings.cordia_phone_number)
                  or phones_match(phone, settings.cordia_test_phone_number)):
        return Sender("opted_out" if status == "opted_out" else "owner")
    # Compared as mailboxes, not as strings. OWNER_EMAIL held
     # "Cordia <Tyler@ai-genpartners.com>" and every reply she sent was dropped
     # as an unknown sender, while outbound kept working because a display name
     # in a To: header is perfectly valid.
    if email and emails_match(email, settings.owner_email):
        return Sender("owner")

    member = None
    if phone:
        member = await family_circle_service.resolve_member_by_phone(db, phone)
    if member is None and email:
        member = await family_circle_service.resolve_member_by_email(db, email)
    if member is not None and member.has_circle_access:
        member_status = status
        if not phone and member.phone:
            member_status = await consent_service.status_for(db, member.phone)
        if member_status == "opted_out":
            return Sender("opted_out", member=member)
        if member_status in ("rejected", "pending"):
            return Sender("unapproved", member=member)
        return Sender("family", member=member)

    return Sender("unknown")
