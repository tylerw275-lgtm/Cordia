import pytest
from sqlalchemy import func, select

from app.models.family import GrandkidActivity
from app.services import family_service
from app.services.family_seed import _find_exact, seed_family


@pytest.mark.asyncio
async def test_seed_creates_the_whole_family(db, seed_doc):
    summary = await seed_family(db, seed_doc)
    members = await family_service.list_family_members(db)
    assert len(members) == len(seed_doc.members)
    assert summary["members_created"] == len(seed_doc.members)

    # Parent links resolve on the second pass.
    child = await _find_exact(db, "Pia Rivers")
    parent = await _find_exact(db, "Dominic Rivers")
    assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_seed_is_idempotent(db, seed_doc):
    await seed_family(db, seed_doc)
    members_before = len(await family_service.list_family_members(db))
    activities_before = (await db.execute(select(func.count()).select_from(GrandkidActivity))).scalar_one()

    summary = await seed_family(db, seed_doc)

    members_after = len(await family_service.list_family_members(db))
    activities_after = (await db.execute(select(func.count()).select_from(GrandkidActivity))).scalar_one()
    assert members_after == members_before
    # The original script re-inserted every activity on each run, skewing the
    # grandkid activity balance a little further each boot.
    assert activities_after == activities_before == len(seed_doc.activities)
    assert summary["members_created"] == 0
    assert summary["activities_created"] == 0


@pytest.mark.asyncio
async def test_seed_backfills_nulls_but_preserves_edits(db, seed_doc):
    await family_service.create_family_member(
        db, name="Dominic Rivers", relationship="son",
        personality_notes="Prefers a phone call to a text.",
    )
    await seed_family(db, seed_doc)

    member = await _find_exact(db, "Dominic Rivers")
    assert member.birthday is not None      # backfilled from the seed
    assert member.city == "Springfield"
    assert "Prefers a phone call to a text." in member.personality_notes  # edit survived
    assert "pool" in member.personality_notes                             # seed note merged


@pytest.mark.asyncio
async def test_seed_never_overwrites_an_existing_value(db, seed_doc):
    await family_service.create_family_member(
        db, name="Theodore Rivers", relationship="son", city="Somewhere Else",
    )
    await seed_family(db, seed_doc)
    member = await _find_exact(db, "Theodore Rivers")
    assert member.city == "Somewhere Else"


@pytest.mark.asyncio
async def test_seed_never_backfills_onto_a_near_miss_name(db, seed_doc):
    """Seeding must match names EXACTLY.

    Rosters contain single-word names. Resolving those through the fuzzy lookup
    matched an unrelated pre-existing row — writing one person's phone, birthday
    and address onto a stranger, and never creating the real row. Because the
    seed runs on every boot, it repeated forever.
    """
    stranger = await family_service.create_family_member(
        db, name="Marta Jane Kowalski", relationship="friend",
    )
    await seed_family(db, seed_doc)

    await db.refresh(stranger)
    assert stranger.relationship == "friend"
    assert stranger.birthday is None
    assert stranger.phone is None
    assert stranger.personality_notes is None

    # ...and the real seed row was created alongside her.
    real = await _find_exact(db, "Marta")
    assert real is not None and real.relationship == "daughter-in-law"


@pytest.mark.asyncio
async def test_daughter_in_law_is_not_rendered_as_a_child(db, seed_doc):
    """A daughter-in-law seeded with a `parent` (to carry spouse context) used
    to render as "child of <husband>" in every prompt."""
    await seed_family(db, seed_doc)
    dil = await _find_exact(db, "Marta")
    assert dil.parent_id is None

    roster = await family_service.get_family_roster_text(db)
    assert "Marta — daughter-in-law" in roster
    assert "Marta — daughter-in-law; child of" not in roster
    # Real children still render their parent.
    assert "child of Dominic Rivers" in roster


@pytest.mark.asyncio
async def test_add_family_member_tool_creates_and_links(db, seed_doc):
    """There was no way to add a new family member from conversation, while the
    roster block asserts Cord already knows everyone."""
    from app.tools import family_tools

    await seed_family(db, seed_doc)
    parent = await _find_exact(db, "Dominic Rivers")

    result = await family_tools.add_family_member_handler(
        db, name="Nora Rivers", relationship="granddaughter",
        birthday="2026-02-14", parent_name="Dominic Rivers", interests=["music"],
    )
    assert result["added"] is True

    nora = await _find_exact(db, "Nora Rivers")
    assert nora.parent_id == parent.id
    assert nora.birthday.isoformat() == "2026-02-14"

    roster = await family_service.get_family_roster_text(db)
    assert "Nora Rivers — granddaughter; child of Dominic Rivers" in roster


@pytest.mark.asyncio
async def test_add_family_member_refuses_a_duplicate(db, seed_doc):
    from app.tools import family_tools

    await seed_family(db, seed_doc)
    result = await family_tools.add_family_member_handler(
        db, name="Dominic Rivers", relationship="son",
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
