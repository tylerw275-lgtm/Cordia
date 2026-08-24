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

def test_the_owner_is_told_to_answer_before_asking():
    text = _owner_text()

    assert "SMALL ASKS: ANSWER, THEN ASK" in text
    assert "Lead with the answer, not the questions." in text


def test_two_questions_is_the_ceiling():
    assert "at most TWO questions" in _owner_text()


def test_questions_are_an_offer_not_a_gate():
    """A follow-up that ignores them must not stall waiting for answers."""
    assert "They are an offer, not a gate." in _owner_text()


def test_it_still_must_not_ask_what_it_could_look_up():
    """The existing discipline has to survive the new mode."""
    text = _owner_text()

    assert "Never ask what you could find out yourself" in text
    assert "that is your job, not theirs" in text


def test_the_project_interview_is_fenced_as_the_exception():
    """Two contradictory instructions reaching the model in one turn is a bug
    this codebase has already had once. The project rule now names itself as
    the exception and says when it applies."""
    text = _owner_text()

    assert "This is the exception to ANSWER, THEN ASK above" in text
    assert "only once start_project has fired" in text
    # and the project path still keeps its own shape
    assert "Send the questions as ONE numbered text" in text


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
