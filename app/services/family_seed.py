"""Load Cordia's family roster into the database.

Runs on every boot (see ``app.main.lifespan``) so a fresh or reset database
self-heals — the original bug was that this data existed only in a manual
script nobody ran, leaving Cord with an empty ``family_members`` table and
nothing to say about the family.

Every operation is create-or-backfill: an existing row is only ever *filled in*
where a column is null, never overwritten. Edits Cordia or the model make
through ``update_family_member_notes`` survive a reseed.
"""
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.family_seed import ACTIVITIES, FAMILY
from app.models.family import GrandkidActivity
from app.services import family_service

logger = logging.getLogger(__name__)

# Columns a reseed may fill in when they are null on an existing row. Excludes
# interests / personality_notes, which merge instead (see _merge_notes).
_BACKFILL_FIELDS = (
    "nickname", "gender", "aliases", "birthday", "anniversary", "phone",
    "email", "address", "city", "state", "school_name", "grade_level",
)


def _merge_notes(member, data: dict[str, Any]) -> bool:
    """Add seed interests/notes the member is missing. Returns True if changed."""
    changed = False
    if data.get("interests"):
        current = list(member.interests or [])
        additions = [i for i in data["interests"] if i not in current]
        if additions:
            member.interests = current + additions
            changed = True
    if data.get("personality_notes"):
        note = data["personality_notes"]
        current_note = member.personality_notes or ""
        if note not in current_note:
            member.personality_notes = f"{current_note}\n{note}".strip() if current_note else note
            changed = True
    return changed


def _backfill(member, data: dict[str, Any]) -> bool:
    """Fill null columns from the seed. Never overwrites an existing value."""
    changed = False
    for field in _BACKFILL_FIELDS:
        if data.get(field) is not None and getattr(member, field, None) is None:
            setattr(member, field, data[field])
            changed = True
    return changed


async def _activity_exists(db: AsyncSession, title: str, activity_date) -> bool:
    result = await db.execute(
        select(GrandkidActivity.id)
        .where(GrandkidActivity.title == title)
        .where(GrandkidActivity.activity_date == activity_date)
    )
    return result.first() is not None


async def seed_family(db: AsyncSession) -> dict:
    """Create any missing family members and historical activities.

    Safe to call repeatedly — running it twice creates no duplicate members and
    no duplicate activities.
    """
    created: list[str] = []
    updated: list[str] = []
    activities_created: list[str] = []

    for data in FAMILY:
        existing = await family_service.get_family_member_by_name(db, data["name"])
        if existing:
            changed = _backfill(existing, data) | _merge_notes(existing, data)
            if changed:
                await db.commit()
                updated.append(data["name"])
            continue
        kwargs = {k: v for k, v in data.items() if k != "parent"}
        member = await family_service.create_family_member(db, **kwargs)
        created.append(member.name)

    # Second pass: parent links, once every member exists.
    for data in FAMILY:
        if "parent" not in data:
            continue
        member = await family_service.get_family_member_by_name(db, data["name"])
        parent = await family_service.get_family_member_by_name(db, data["parent"])
        if member is not None and parent is not None and member.parent_id is None:
            member.parent_id = parent.id
            await db.commit()

    for act in ACTIVITIES:
        if await _activity_exists(db, act["title"], act["activity_date"]):
            continue
        await family_service.log_grandkid_activity(db, **act)
        activities_created.append(act["title"])

    return {
        "members_created": len(created),
        "members_updated": len(updated),
        "activities_created": len(activities_created),
        "created": created,
        "updated": updated,
    }
