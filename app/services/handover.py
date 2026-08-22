"""Making the deployment hers.

Everything built while an assistant is being tested belongs to whoever was
testing it. The projects, the memories, the tasks, the conversations — all of it
was created under "the owner", and on the day the owner becomes a different
person it is sitting there looking like her history.

This is the cleanup, and it is deliberately conservative in three ways.

**It never touches consent records.** `sms_consent` and `consent_submissions`
are the compliance evidence for a registered 10DLC campaign. They are not test
data even when they were created during testing, and nothing here may delete
them.

**It never touches the usage ledger.** `usage_events` was reconciled against a
real invoice. Wiping it would make the spend figures lie.

**It does nothing until told twice.** Every function reports what it would do
and changes nothing unless `apply=True`. A handover happens once and is not
undoable, so the default is a description.
"""
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.memory import Memory
from app.models.project import Project
from app.models.task import FamilyTask

logger = logging.getLogger(__name__)

# Kept, always. Named here so a future edit has to argue with the list rather
# than quietly widen the delete.
PRESERVED = (
    "sms_consent",          # legal consent evidence for the 10DLC campaign
    "consent_submissions",  # the names people typed on the public form
    "usage_events",         # reconciled against a real invoice
    "family_members",       # the roster, and it is hers already
    "authorized_users",     # principals; renamed, never dropped
    "contacts",             # her address book
    "leases",               # her property records
)


@dataclass
class Plan:
    """What a handover would remove, before anything is removed."""
    projects: int = 0
    memories: int = 0
    tasks: int = 0
    conversations: int = 0
    messages: int = 0
    preserved: tuple = field(default_factory=lambda: PRESERVED)

    @property
    def total(self) -> int:
        return self.projects + self.memories + self.tasks + self.conversations

    def describe(self, applied: bool = False) -> str:
        """Past tense once it has happened.

        A script that says "would remove" after removing it reads as though it
        did nothing, which is the wrong thing to believe at the one moment this
        gets run.
        """
        if not self.total and not self.messages:
            return "Nothing to clear — this deployment has no test data."
        verb, kept = ("Removed", "Kept") if applied else ("Would remove", "Would keep")
        return (
            f"{verb}: {self.projects} project(s), {self.memories} memory/ies, "
            f"{self.tasks} task(s), {self.conversations} conversation(s) "
            f"carrying {self.messages} message(s).\n"
            f"{kept}, untouched: {', '.join(self.preserved)}."
        )


async def survey(db: AsyncSession) -> Plan:
    """What is here, without changing any of it."""
    async def count(model) -> int:
        return (await db.execute(select(func.count(model.id)))).scalar() or 0

    return Plan(
        projects=await count(Project),
        memories=await count(Memory),
        tasks=await count(FamilyTask),
        conversations=await count(Conversation),
        messages=await count(Message),
    )


async def clear_test_data(db: AsyncSession, apply: bool = False) -> Plan:
    """Remove the working history so she starts on a clean page.

    Messages go with their conversations by cascade. Feature requests are
    memories too and go with them — they have already been emailed to the
    operator, so the record survives outside the database.
    """
    plan = await survey(db)
    if not apply:
        return plan

    # Order matters only for readability; none of these reference each other.
    await db.execute(delete(FamilyTask))
    await db.execute(delete(Project))
    await db.execute(delete(Memory))
    await db.execute(delete(Message))
    await db.execute(delete(Conversation))
    await db.commit()
    logger.warning(
        f"Handover: cleared {plan.projects} projects, {plan.memories} memories, "
        f"{plan.tasks} tasks, {plan.conversations} conversations"
    )
    return plan


async def owner_check(db: AsyncSession) -> dict:
    """Whether the account holder on file is the person it is being given to.

    The failure this catches: change CORDIA_PHONE_NUMBER and OWNER_EMAIL, and
    until the app reseeds, the principal row still carries the tester's
    details. She then misses the principal lookup, resolves as an anonymous
    owner through the config fallback, and is shown everything — that path
    applies no visibility scoping.
    """
    from app.config import settings
    from app.services import principal_service
    from app.utils.email_address import normalize_email
    from app.utils.phone import normalize_phone

    owner = await principal_service.get_owner(db)
    if owner is None:
        return {"ok": False, "reason": "no_owner_on_file",
                "fix": "Set CORDIA_PHONE_NUMBER / OWNER_EMAIL and restart, or add her "
                       "on the dashboard."}

    phone_matches = (
        not settings.cordia_phone_number
        or normalize_phone(owner.phone) == normalize_phone(settings.cordia_phone_number)
    )
    email_matches = (
        not settings.owner_email
        or normalize_email(owner.email) == normalize_email(settings.owner_email)
    )
    return {
        "ok": phone_matches and email_matches,
        "owner_name": owner.name,
        "phone_matches_config": phone_matches,
        "email_matches_config": email_matches,
        "fix": None if (phone_matches and email_matches) else (
            "The principal row does not match the configured owner. Restart the app "
            "to reseed, or correct it on the dashboard's principals card."
        ),
    }
