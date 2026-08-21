"""Who is a principal, and what may they see.

Two ideas, deliberately kept apart:

- **Identity** — resolving an inbound phone number or email address to a
  principal. Cordia, Tom and Karie each get a full Cord; a family-circle member
  gets the restricted one; anyone else gets nothing.
- **Access** — what one principal may see of another's. The default is nothing.
  Everything crosses only through a grant Cordia made deliberately, and grants
  are revocable.

A briefing is *not* a grant. Telling Karie about a trip once must not subscribe
her to every future update about it — that distinction is the whole point of the
design and is why `brief` writes no row here.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.authorized_user import AccessGrant, AuthorizedUser
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

# Areas a grant can cover, beyond a single project by id.
SHAREABLE_SCOPES = ("loyalty", "travel_prefs", "leases", "family_notes", "projects", "memories")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

async def resolve_by_phone(db: AsyncSession, phone: str) -> AuthorizedUser | None:
    digits = normalize_phone(phone)
    if not digits:
        return None
    for user in (await db.execute(
        select(AuthorizedUser).where(AuthorizedUser.is_active.is_(True))
        .where(AuthorizedUser.phone.isnot(None))
    )).scalars():
        if normalize_phone(user.phone) == digits:
            return user
    return None


async def resolve_by_email(db: AsyncSession, email: str) -> AuthorizedUser | None:
    address = (email or "").strip().lower()
    if not address:
        return None
    rows = (await db.execute(
        select(AuthorizedUser)
        .where(AuthorizedUser.is_active.is_(True))
        .where(AuthorizedUser.email.isnot(None))
    )).scalars()
    # Compare in Python rather than SQL so casing and stray whitespace on a
    # hand-entered address cannot cause a silent miss.
    return next((u for u in rows if (u.email or "").strip().lower() == address), None)


async def get_owner(db: AsyncSession) -> AuthorizedUser | None:
    return (await db.execute(
        select(AuthorizedUser).where(AuthorizedUser.is_owner.is_(True))
        .where(AuthorizedUser.is_active.is_(True))
    )).scalars().first()


async def find_by_name(db: AsyncSession, name: str) -> AuthorizedUser | None:
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for user in (await db.execute(
        select(AuthorizedUser).where(AuthorizedUser.is_active.is_(True))
    )).scalars():
        low = user.name.lower()
        if low == needle or low.split()[0] == needle or needle in low:
            return user
    return None


async def list_principals(db: AsyncSession) -> list[AuthorizedUser]:
    return list((await db.execute(
        select(AuthorizedUser).order_by(AuthorizedUser.is_owner.desc(), AuthorizedUser.name)
    )).scalars())


async def seed_principals(db: AsyncSession) -> int:
    """Load principals from PRINCIPALS_JSON, idempotently.

    Deliberately config, not source: these are real names, mobile numbers and
    addresses, and the family roster is kept out of the repo for the same
    reason. Falls back to creating Cordia from the existing single-owner config
    so a deployment that has not set the variable still resolves her.

    Shape: [{"name": "...", "phone": "...", "email": "...", "is_owner": true}]
    """
    entries: list[dict] = []
    if settings.principals_json:
        try:
            parsed = json.loads(settings.principals_json)
            entries = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError as e:
            # Log the failure, never the value — it holds phone numbers.
            logger.error(f"PRINCIPALS_JSON is not valid JSON ({e.msg} at position {e.pos})")
            entries = []

    if not entries and (settings.cordia_phone_number or settings.owner_email):
        entries = [{
            "name": "Cordia", "phone": settings.cordia_phone_number,
            "email": settings.owner_email, "is_owner": True,
        }]

    added = 0
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        phone = str(entry.get("phone") or "").strip() or None
        email = (str(entry.get("email") or "").strip().lower() or None)

        existing = None
        if phone:
            existing = await resolve_by_phone(db, phone)
        if existing is None and email:
            existing = await resolve_by_email(db, email)
        if existing is None:
            existing = await find_by_name(db, name)

        if existing is None:
            db.add(AuthorizedUser(
                name=name[:120], phone=phone, email=email,
                is_owner=bool(entry.get("is_owner")),
                notes=str(entry.get("notes") or "") or None,
            ))
            added += 1
        else:
            # Fill in gaps without clobbering anything already set by hand.
            existing.phone = existing.phone or phone
            existing.email = existing.email or email
            if entry.get("is_owner"):
                existing.is_owner = True
    await db.commit()
    if added:
        logger.info(f"Seeded {added} principal(s)")
    return added


async def add_principal(
    db: AsyncSession, name: str, phone: str | None = None, email: str | None = None,
) -> tuple[AuthorizedUser | None, str]:
    """Add someone by hand, from the dashboard. Returns (user, outcome).

    Requires at least one contact route. A principal with neither a phone nor an
    email can never be resolved from an inbound message or reached by one, so
    accepting it would create a row that looks right on the page and does
    nothing at all.

    Never mints an owner: there is exactly one account holder and this form is
    not how she would be replaced.
    """
    name = (name or "").strip()
    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None

    if not name:
        return None, "no_name"
    if not phone and not email:
        return None, "no_contact_route"

    existing = None
    if phone:
        existing = await resolve_by_phone(db, phone)
    if existing is None and email:
        existing = await resolve_by_email(db, email)

    if existing is not None:
        # Fill in what's missing rather than creating a second row for the same
        # person — a duplicate would split their conversation history in two.
        existing.name = name[:120] or existing.name
        existing.phone = phone or existing.phone
        existing.email = email or existing.email
        existing.is_active = True
        await db.commit()
        return existing, "updated"

    user = AuthorizedUser(name=name[:120], phone=phone, email=email, is_owner=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(f"Added principal {user.name}")
    return user, "added"


async def set_active(db: AsyncSession, user_id, active: bool) -> bool:
    """Turn someone's access off without deleting them.

    Deactivating rather than deleting keeps their conversation history and any
    grants coherent, and resolve_by_phone/email already filter on is_active, so
    an inactive principal simply stops being recognised.
    """
    user = (await db.execute(
        select(AuthorizedUser).where(AuthorizedUser.id == user_id)
    )).scalars().first()
    if user is None or user.is_owner:
        return False
    user.is_active = active
    await db.commit()
    return True


def config_health() -> tuple[str, str]:
    """(status, human explanation) for PRINCIPALS_JSON — malformed JSON otherwise
    only ever appears in a boot log."""
    if not settings.principals_json:
        return "unset", "PRINCIPALS_JSON is not set in Railway."
    try:
        parsed = json.loads(settings.principals_json)
    except json.JSONDecodeError as e:
        return "invalid", f"PRINCIPALS_JSON is not valid JSON ({e.msg} at position {e.pos})."
    if not isinstance(parsed, list):
        return "invalid", "PRINCIPALS_JSON must be a list of people."
    return "ok", f"PRINCIPALS_JSON holds {len(parsed)} entr{'y' if len(parsed) == 1 else 'ies'}."


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

async def grant(
    db: AsyncSession, grantee: AuthorizedUser, scope: str,
    resource_id: str | None = None, granted_by: AuthorizedUser | None = None,
) -> bool:
    """Open one door. Idempotent — re-granting an open door is a no-op."""
    if await has_access(db, grantee, scope, resource_id):
        return False
    db.add(AccessGrant(
        grantee_id=grantee.id, scope=scope, resource_id=resource_id,
        granted_by_id=granted_by.id if granted_by else None,
    ))
    await db.commit()
    logger.info(f"Granted {scope} to {grantee.name}")
    return True


async def revoke(
    db: AsyncSession, grantee: AuthorizedUser, scope: str, resource_id: str | None = None
) -> int:
    """Close a door. Rows are marked revoked, not deleted — 'who could see this,
    and when' stays answerable."""
    rows = (await db.execute(
        select(AccessGrant)
        .where(AccessGrant.grantee_id == grantee.id)
        .where(AccessGrant.scope == scope)
        .where(AccessGrant.revoked_at.is_(None))
    )).scalars().all()
    now = datetime.now(timezone.utc)
    closed = 0
    for row in rows:
        if resource_id and row.resource_id != resource_id:
            continue
        row.revoked_at = now
        closed += 1
    await db.commit()
    return closed


async def has_access(
    db: AsyncSession, user: AuthorizedUser | None, scope: str, resource_id: str | None = None
) -> bool:
    """Whether `user` may see something of the owner's.

    The owner always can — it is hers. Everyone else needs a live grant, either
    on the whole area or on that one resource.
    """
    if user is None:
        return False
    if user.is_owner:
        return True
    rows = (await db.execute(
        select(AccessGrant)
        .where(AccessGrant.grantee_id == user.id)
        .where(AccessGrant.revoked_at.is_(None))
        .where(AccessGrant.scope == scope)
    )).scalars().all()
    for row in rows:
        # A grant with no resource_id covers the whole area.
        if row.resource_id is None or row.resource_id == resource_id:
            return True
    return False


async def visible_scope(db: AsyncSession, user: AuthorizedUser | None, scope: str) -> tuple[list, bool]:
    """What this user may read for a scope.

    Returns (owner ids, may_read_unowned). The second half matters more than it
    looks: rows created before multi-user carry a NULL owner and are Cordia's.
    Treating NULL as "everyone's" would hand her entire history — memories,
    projects — to Tom and Karie the moment this shipped, which is precisely the
    leak this whole design exists to prevent.
    """
    if user is None:
        return [], False
    if user.is_owner:
        return [user.id], True
    ids = [user.id]
    shared = await has_access(db, user, scope)
    if shared:
        owner = await get_owner(db)
        if owner is not None:
            ids.append(owner.id)
    return ids, shared


async def visible_owner_ids(db: AsyncSession, user: AuthorizedUser | None, scope: str) -> list:
    ids, _ = await visible_scope(db, user, scope)
    return ids


def scope_filter(column, allowed_ids: list, may_read_unowned: bool):
    """A SQLAlchemy predicate limiting rows to what a principal may see."""
    clause = column.in_(allowed_ids)
    return or_(clause, column.is_(None)) if may_read_unowned else clause
