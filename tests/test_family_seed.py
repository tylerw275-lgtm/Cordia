import pytest
from sqlalchemy import func, select

from app.data.family_seed import ACTIVITIES, FAMILY
from app.models.family import GrandkidActivity
from app.services import family_service
from app.services.family_seed import seed_family


@pytest.mark.asyncio
async def test_seed_creates_the_whole_family(db):
    summary = await seed_family(db)
    members = await family_service.list_family_members(db)
    assert len(members) == len(FAMILY)
    assert summary["members_created"] == len(FAMILY)

    # Parent links resolve on the second pass.
    brighton = await family_service.get_family_member_by_name(db, "Brighton Wilkinson")
    aaron = await family_service.get_family_member_by_name(db, "Aaron Wilkinson")
    assert brighton.parent_id == aaron.id


@pytest.mark.asyncio
async def test_seed_is_idempotent(db):
    await seed_family(db)
    members_before = len(await family_service.list_family_members(db))
    activities_before = (await db.execute(select(func.count()).select_from(GrandkidActivity))).scalar_one()

    summary = await seed_family(db)

    members_after = len(await family_service.list_family_members(db))
    activities_after = (await db.execute(select(func.count()).select_from(GrandkidActivity))).scalar_one()
    assert members_after == members_before
    # The original script re-inserted every activity on each run, skewing the
    # grandkid activity balance a little further each boot.
    assert activities_after == activities_before == len(ACTIVITIES)
    assert summary["members_created"] == 0
    assert summary["activities_created"] == 0


@pytest.mark.asyncio
async def test_seed_backfills_nulls_but_preserves_edits(db):
    await family_service.create_family_member(
        db, name="Aaron Wilkinson", relationship="son",
        personality_notes="Prefers a phone call to a text.",
    )
    await seed_family(db)

    aaron = await family_service.get_family_member_by_name(db, "Aaron Wilkinson")
    assert aaron.birthday is not None      # backfilled from the seed
    assert aaron.city == "Franklin"
    assert "Prefers a phone call to a text." in aaron.personality_notes  # edit survived
    assert "pool" in aaron.personality_notes                              # seed note merged


@pytest.mark.asyncio
async def test_seed_never_overwrites_an_existing_value(db):
    await family_service.create_family_member(
        db, name="Tyler Wilkinson", relationship="son", city="Virginia Beach",
    )
    await seed_family(db)
    tyler = await family_service.get_family_member_by_name(db, "Tyler Wilkinson")
    assert tyler.city == "Virginia Beach"
