import pytest
from sqlalchemy import func, select

from app.data.family_seed import ACTIVITIES, FAMILY
from app.models.family import GrandkidActivity
from app.services import family_service
from app.services.family_seed import _find_exact, seed_family


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


@pytest.mark.asyncio
async def test_seed_never_backfills_onto_a_near_miss_name(db):
    """Seeding must match names EXACTLY.

    The roster contains single-word names ("Sarah", "Dick", "Verna"). Resolving
    those through the fuzzy lookup matched an unrelated pre-existing row —
    writing a daughter-in-law's phone, birthday and address onto a stranger,
    and never creating the real row. Because the seed runs on every boot, it
    repeated forever.
    """
    stranger = await family_service.create_family_member(
        db, name="Sarah Jane Kowalski", relationship="friend",
    )
    await seed_family(db)

    await db.refresh(stranger)
    assert stranger.relationship == "friend"
    assert stranger.birthday is None
    assert stranger.phone is None
    assert stranger.personality_notes is None

    # ...and the real seed row was created alongside her.
    real = await _find_exact(db, "Sarah")
    assert real is not None and real.relationship == "daughter-in-law"


@pytest.mark.asyncio
async def test_daughter_in_law_is_not_rendered_as_a_child(db):
    """Amber was seeded with Aaron as `parent` to carry spouse context, which
    the roster rendered as "child of Aaron" in every prompt."""
    await seed_family(db)
    amber = await _find_exact(db, "Amber Wilkinson")
    assert amber.parent_id is None

    roster = await family_service.get_family_roster_text(db)
    assert "Amber Wilkinson — daughter-in-law" in roster
    assert "Amber Wilkinson — daughter-in-law; child of" not in roster
    # Real children still render their parent.
    assert "child of Aaron Wilkinson" in roster


@pytest.mark.asyncio
async def test_add_family_member_tool_creates_and_links(db):
    """There was no way to add a new family member from conversation, while the
    roster block asserts Cord already knows everyone."""
    from app.tools import family_tools

    await seed_family(db)
    aaron = await _find_exact(db, "Aaron Wilkinson")

    result = await family_tools.add_family_member_handler(
        db, name="Nora Wilkinson", relationship="granddaughter",
        birthday="2026-02-14", parent_name="Aaron Wilkinson", interests=["music"],
    )
    assert result["added"] is True

    nora = await _find_exact(db, "Nora Wilkinson")
    assert nora.parent_id == aaron.id
    assert nora.birthday.isoformat() == "2026-02-14"

    roster = await family_service.get_family_roster_text(db)
    assert "Nora Wilkinson — granddaughter; child of Aaron Wilkinson" in roster


@pytest.mark.asyncio
async def test_add_family_member_refuses_a_duplicate(db):
    from app.tools import family_tools

    await seed_family(db)
    result = await family_tools.add_family_member_handler(
        db, name="Aaron Wilkinson", relationship="son",
    )
    assert result["added"] is False
    assert "already in the family profiles" in result["message"]


@pytest.mark.asyncio
async def test_add_family_member_rejects_a_bad_birthday(db):
    from app.tools import family_tools

    result = await family_tools.add_family_member_handler(
        db, name="Test Person", relationship="friend", birthday="Feb 3rd",
    )
    assert result["added"] is False
    assert await _find_exact(db, "Test Person") is None
