"""The family roster block: Cord must see the family without calling a tool,
and must never surface aliases or contact details."""
from datetime import date

import pytest

from app.prompts.system_prompt import build_family_system_prompt, build_system_prompt
from app.services import family_service
from app.services.family_seed import seed_family


@pytest.mark.asyncio
async def test_roster_lists_everyone_with_relationships(db, seed_doc):
    await seed_family(db, seed_doc)
    roster = await family_service.get_family_roster_text(db)

    for member in seed_doc.members:
        assert member.name in roster
    assert "son" in roster and "granddaughter" in roster
    assert "child of Dominic Rivers" in roster   # parent links render


@pytest.mark.asyncio
async def test_roster_omits_aliases_and_contact_details(db, seed_doc):
    await seed_family(db, seed_doc)
    roster = await family_service.get_family_roster_text(db)

    # The prompt forbids Cord from ever acknowledging the alias mapping or
    # reading back stored contact details — so neither belongs in the prompt.
    # Derived from the document rather than hardcoded, so this can't rot when a
    # member gains a contact field.
    for member in seed_doc.members:
        for alias in member.aliases or []:
            assert alias not in roster, f"alias {alias} leaked into the roster"
        for detail in (member.phone, member.email, member.address):
            if detail:
                assert detail not in roster, f"contact detail leaked into the roster"
    # city is rendered deliberately
    assert "Springfield" in roster


@pytest.mark.asyncio
async def test_empty_family_renders_an_explicit_no_data_block(db):
    roster = await family_service.get_family_roster_text(db)
    assert "none are loaded" in roster
    assert "Do not invent" in roster


def test_roster_is_byte_stable_across_calls():
    class M:
        id, parent_id, nickname, state, anniversary = 1, None, None, "IL", None
        name, relationship, city = "Dominic Rivers", "son", "Springfield"
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
    # The roster is the last block that's stable across channel and context;
    # everything after it varies per turn and must stay outside the cached prefix.
    assert blocks[marked[0]]["text"].strip().startswith("FAMILY ROSTER")
    assert marked[0] == len(blocks) - 3  # + channel format + module context


def test_email_channel_gets_email_formatting_rules():
    """Email replies were written under the SMS rules — 3-4 sentences, no
    markdown — and then sent as email."""
    sms = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R", channel="sms"))
    email = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R", channel="email"))

    assert "RESPONSE FORMAT FOR SMS" in sms
    assert "Maximum 3-4 sentences" in sms
    assert "RESPONSE FORMAT FOR EMAIL" in email
    assert "Maximum 3-4 sentences" not in email
    assert "Length limits do not apply" in email


def test_prompt_never_names_a_tool_that_is_not_registered(mocker):
    """The prompt used to instruct Cord to use nine outbound/contact tools that
    aren't registered when enable_outbound is off — in the same message that
    forbids offering anything outside CURRENT CAPABILITIES."""
    from app.tools.registry import get_tool_schemas

    mocker.patch("app.config.settings.enable_outbound", False)
    registered = {t["name"] for t in get_tool_schemas("owner")}
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R"))

    candidates = [
        "add_contact", "update_contact", "find_contact", "list_contacts",
        "create_outbound_drafts", "send_outbound", "edit_outbound_draft",
        "invite_to_sms", "list_sms_roster",
    ]
    named_but_missing = [t for t in candidates if t in prompt and t not in registered]
    assert named_but_missing == []


def test_outbound_workflow_returns_when_the_flag_is_on(mocker):
    mocker.patch("app.config.settings.enable_outbound", True)
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R"))
    assert "create_outbound_drafts" in prompt
    assert "invite_to_sms" in prompt


def test_cache_breakpoint_still_set_without_a_roster():
    blocks = build_system_prompt(None, family_roster=None)
    assert sum("cache_control" in b for b in blocks) == 1


def test_family_role_prompt_never_includes_the_roster():
    # Family-circle members must not see Cordia's private family data.
    blocks = build_family_system_prompt("Marta")
    text = " ".join(b["text"] for b in blocks)
    assert "FAMILY ROSTER" not in text
    assert "Nico" not in text  # the fixture's alias


# ---------------------------------------------------------------------------
# The prompt itself must carry no family facts. Everything person-specific
# reaches the model through the roster block, which is built from the database.
# ---------------------------------------------------------------------------

def test_prompt_carries_no_family_facts_of_its_own(seed_doc):
    """With no roster, the assembled prompt must not name anyone or state a
    personal fact. This is the guard that stops a name creeping back in."""
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster=None))

    for member in seed_doc.members:
        assert member.name not in prompt
        first = member.name.split()[0]
        assert first not in prompt, f"{first} appears in the prompt itself"
        for alias in member.aliases or []:
            assert alias not in prompt
        for interest in member.interests or []:
            # a bare interest word may legitimately appear; a *named* pairing may not
            assert f"{first} {interest}" not in prompt
        if member.city:
            assert member.city not in prompt


def test_prompt_has_no_hardcoded_alias_mapping(seed_doc):
    """The mapping lives in the aliases column now, not in the prompt."""
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R"))
    for member in seed_doc.members:
        for alias in member.aliases or []:
            assert alias not in prompt


def test_prompt_still_explains_how_to_resolve_an_unfamiliar_name():
    """Removing the mapping must not remove the behaviour."""
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R"))
    assert "get_family_member" in prompt
    assert "former name" in prompt.lower()


def test_prompt_no_longer_instructs_denial():
    """The old rule told Cord to deny a name difference even if asked directly."""
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster="R"))
    assert "not even if she asks directly" not in prompt
    assert "must remain invisible to her" not in prompt
    assert "answer honestly" in prompt


@pytest.mark.asyncio
async def test_family_facts_reach_the_model_through_the_roster(db, seed_doc):
    """The facts deleted from the prompt must still arrive, via the roster."""
    await seed_family(db, seed_doc)
    roster = await family_service.get_family_roster_text(db)
    prompt = " ".join(b["text"] for b in build_system_prompt(None, family_roster=roster))

    for member in seed_doc.members:
        assert member.name in prompt
        for interest in member.interests or []:
            assert interest in prompt
    # parent links, so "which of her sons is this child's father" is answerable
    assert "child of Dominic Rivers" in prompt


@pytest.mark.asyncio
async def test_long_personality_notes_are_not_truncated_to_200(db):
    """Detail that moved out of the prompt lands in personality_notes; a 200-char
    cap silently dropped the tail of the longest profile."""
    note = "A. " + ("detail " * 40) + "END-OF-NOTE"
    assert 200 < len(note) <= 400
    await family_service.create_family_member(
        db, name="Long Note Person", relationship="granddaughter", personality_notes=note,
    )
    roster = await family_service.get_family_roster_text(db)
    assert "END-OF-NOTE" in roster
