"""The family roster block: Cord must see the family without calling a tool,
and must never surface aliases or contact details."""
from datetime import date

import pytest

from app.prompts.system_prompt import build_family_system_prompt, build_system_prompt
from app.services import family_service
from app.services.family_seed import seed_family


@pytest.mark.asyncio
async def test_roster_lists_everyone_with_relationships(db):
    await seed_family(db)
    roster = await family_service.get_family_roster_text(db)

    for name in ("Aaron Wilkinson", "Ryan Wilkinson", "Tyler Wilkinson", "Bea Wilkinson"):
        assert name in roster
    assert "son" in roster and "grandson" in roster
    assert "child of Aaron Wilkinson" in roster   # parent links render


@pytest.mark.asyncio
async def test_roster_omits_aliases_and_contact_details(db):
    await seed_family(db)
    roster = await family_service.get_family_roster_text(db)

    # The prompt forbids Cord from ever acknowledging the alias mapping or
    # reading back stored contact details — so neither belongs in the prompt.
    assert "Brad" not in roster
    assert "Hunter" not in roster
    assert "6158539483" not in roster
    assert "wilkinson.bea@icloud.com" not in roster
    assert "1019 Amelia Park" not in roster


@pytest.mark.asyncio
async def test_empty_family_renders_an_explicit_no_data_block(db):
    roster = await family_service.get_family_roster_text(db)
    assert "none are loaded" in roster
    assert "Do not invent" in roster


def test_roster_is_byte_stable_across_calls():
    class M:
        id, parent_id, nickname, state, anniversary = 1, None, None, "TN", None
        name, relationship, city = "Aaron Wilkinson", "son", "Franklin"
        birthday = date(1983, 8, 7)
        interests = ["swimming", "golf"]
        personality_notes = "Built a pool."

    first = family_service.format_family_roster([M()], today=date(2026, 8, 20))
    second = family_service.format_family_roster([M()], today=date(2026, 8, 20))
    assert first == second  # prompt-cache hits depend on this


def test_exactly_one_cache_breakpoint_on_the_last_stable_block():
    blocks = build_system_prompt("trip_planning", family_roster="FAMILY ROSTER: ...")
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    assert len(marked) == 1
    # The roster is the last stable block; the module context comes after it.
    assert blocks[marked[0]]["text"].strip().startswith("FAMILY ROSTER")
    assert marked[0] == len(blocks) - 2


def test_cache_breakpoint_still_set_without_a_roster():
    blocks = build_system_prompt(None, family_roster=None)
    assert sum("cache_control" in b for b in blocks) == 1


def test_family_role_prompt_never_includes_the_roster():
    # Family-circle members must not see Cordia's private family data.
    blocks = build_family_system_prompt("Kristen")
    text = " ".join(b["text"] for b in blocks)
    assert "FAMILY ROSTER" not in text
    assert "Brad" not in text
