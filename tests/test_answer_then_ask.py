"""Nobody volunteers the detail that would make the answer good.

Tom's first message was "In New York this afternoon. Can you suggest something
to do?" — short, underspecified, and never going to arrive any other way. Cord
had two modes: answer it literally, or run a full project interview that sends
3-5 numbered questions and waits. The first produces a guess; the second is
right for outfitting a house and wrong for a man standing in Manhattan.

So there is a third mode now: answer with what you have, then ask at most two
questions that would change it.

Also here: a family turn used to carry the member's name and nothing else, so
Cord asked about interests, kids and hometown that were already on file.
"""
import pytest
import pytest_asyncio

from app.models.family import FamilyMember
from app.prompts.system_prompt import (
    FAMILY_SYSTEM_PROMPT,
    build_family_system_prompt,
    build_system_prompt,
)
from app.services.claude_service import _build_family_system, _known_about


def _owner_text() -> str:
    return " ".join(b["text"] for b in build_system_prompt())


# --- the rule itself ---------------------------------------------------------

def test_it_is_told_to_assume_rather_than_interrogate():
    """She said it outright: "You ask too many questions." Five questions came
    back on "A gift for Amber?" — an ask that carries no playbook and is not
    deep work, so the model had chosen the project interview for itself."""
    text = _owner_text()

    assert "ASSUME, DELIVER, THEN OFFER TO ADJUST" in text
    assert "WORK OUT THE MOST LIKELY READING, DO THE WORK, AND SAY WHAT YOU ASSUMED" in text


def test_a_splitting_choice_is_answered_both_ways_not_asked():
    """Object or experience, her bed or the guest rooms — cover both."""
    text = _owner_text()

    assert "WHERE A CHOICE WOULD SPLIT THE ANSWER, COVER BOTH SIDES INSTEAD OF ASKING" in text


def test_one_optional_additive_question_is_the_ceiling():
    text = _owner_text()

    assert "At most ONE question" in text
    assert "ADD something rather than unblock you" in text


def test_it_must_never_end_a_turn_having_only_asked():
    text = _owner_text()

    assert "NEVER end a turn having only asked." in text
    assert "a good answer to the likely question beats no answer to the exact one" in text


def test_an_answer_from_her_finishes_the_work_in_that_turn():
    """She sent "$200" and got nothing back at all."""
    assert 'She said "$200" and got nothing back' in _owner_text()


def test_assumptions_are_named_as_levers_not_asked_about():
    assert "Close by naming the levers, not by asking about them" in _owner_text()


def test_defaults_are_applied_silently():
    text = _owner_text()

    assert "a gift runs $100-200 unless she says otherwise" in text
    assert "6,800 sq ft with four bedrooms" in text


def test_it_still_must_not_ask_what_it_could_look_up():
    """The existing discipline has to survive the rewrite."""
    assert "Never ask what you could find out yourself" in _owner_text()


# --- the project interview must stop swallowing ordinary asks ----------------

def test_a_project_is_not_merely_something_that_needs_research():
    """"A gift for Amber?" routes to no playbook and is not deep work, yet
    produced a five-question interview — the model selected project mode
    itself, and the old wording invited it."""
    text = _owner_text()

    assert "It is NOT for anything that merely needs research" in text
    assert "If you could deliver something useful in this turn, it is not a project" in text


def test_even_a_project_delivers_before_it_asks():
    text = _owner_text()

    assert "deliver a first pass before you ask anything" in text
    assert "always alongside the first pass — never instead of it" in text


def test_nothing_tells_it_to_stop_and_wait_any_more():
    """The instruction that produced a questionnaire with no work attached."""
    text = _owner_text()

    assert "stop and wait" not in text
    assert "Do not answer the request in the same message" not in text


def test_family_gets_the_same_rule():
    text = build_family_system_prompt("Tom")[0]["text"]

    assert "ANSWER, THEN ASK:" in text
    assert "Never send questions on their own." in text


# --- what Cord already knows -------------------------------------------------

@pytest_asyncio.fixture
async def tom(db):
    member = FamilyMember(
        name="Tom Harrington", nickname="Tom", relationship="son",
        city="Nashville", state="TN",
        interests=["golf", "barbecue"],
        personality_notes="Hates crowds. Early riser.",
        has_circle_access=True, phone="+16155550143",
        email="tom@example.com", address="12 Private Lane",
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@pytest.mark.asyncio
async def test_it_knows_who_it_is_talking_to(db, tom):
    known = await _known_about(db, tom)

    assert "Nashville, TN" in known
    assert "golf" in known and "barbecue" in known
    assert "Hates crowds" in known
    assert "son" in known


@pytest.mark.asyncio
async def test_it_never_gets_handed_their_contact_details(db, tom):
    """The family prompt forbids reading a stored number or address back. The
    surest way to honour that is not to put them in front of the model."""
    known = await _known_about(db, tom)

    assert "+16155550143" not in known
    assert "tom@example.com" not in known
    assert "12 Private Lane" not in known


@pytest.mark.asyncio
async def test_a_childs_interests_come_along(db, tom):
    db.add(FamilyMember(name="Ruby", relationship="granddaughter",
                        parent_id=tom.id, grade_level="4th",
                        interests=["horses"]))
    await db.commit()

    known = await _known_about(db, tom)

    assert "Ruby" in known
    assert "horses" in known


@pytest.mark.asyncio
async def test_another_family_members_details_stay_out(db, tom):
    """Their own data only — the privacy wall between family members holds."""
    db.add(FamilyMember(name="Karie", relationship="daughter",
                        city="Memphis", interests=["sailing"]))
    await db.commit()

    known = await _known_about(db, tom)

    assert "Karie" not in known
    assert "sailing" not in known


@pytest.mark.asyncio
async def test_it_reaches_the_prompt_tom_actually_gets(db, tom):
    text = (await _build_family_system(db, tom))[0]["text"]

    assert "golf" in text
    assert "WHAT YOU ALREADY KNOW ABOUT Tom Harrington" in text


@pytest.mark.asyncio
async def test_someone_brand_new_still_gets_their_relationship(db):
    """`relationship` is non-null, so there is always at least one fact —
    which is why the empty case below is a floor, not the normal path."""
    member = FamilyMember(name="New Person", relationship="cousin", has_circle_access=True)
    db.add(member)
    await db.commit()
    await db.refresh(member)

    text = (await _build_family_system(db, member))[0]["text"]

    assert "Cordia's cousin" in text
    assert "{known}" not in text


def test_an_empty_known_block_does_not_leave_a_hole():
    text = build_family_system_prompt("Tom", "", "")[0]["text"]

    assert "Nothing on file yet" in text


def test_the_template_has_no_unfilled_slots():
    text = build_family_system_prompt("Tom", "some request", "- Likes golf.")[0]["text"]

    for slot in ("{member_name}", "{open_requests}", "{known}", "{current_date}"):
        assert slot not in text
    assert "Likes golf" in text
