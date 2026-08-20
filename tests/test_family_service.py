import pytest
from datetime import date, timedelta

from app.services import family_service


@pytest.mark.asyncio
async def test_create_and_retrieve_family_member(db):
    member = await family_service.create_family_member(
        db,
        name="Emma",
        relationship="granddaughter",
        city="Nashville",
        state="TN",
        birthday=date(2016, 3, 15),
        grade_level="4th",
        interests=["dinosaurs", "art", "swimming"],
        personality_notes="Energetic, loves learning, gets excited about animals",
    )
    assert member.id is not None

    found = await family_service.get_family_member_by_name(db, "Emma")
    assert found is not None
    assert found.interests == ["dinosaurs", "art", "swimming"]
    assert found.grade_level == "4th"


@pytest.mark.asyncio
async def test_list_upcoming_events(db):
    event = await family_service.create_family_event(
        db,
        title="Summer Family Reunion",
        event_type="gathering",
        event_date=date.today() + timedelta(days=30),
        recurrence="annual",
    )
    events = await family_service.list_upcoming_events(db, days_ahead=365)
    assert any(e.id == event.id for e in events)


# ---------------------------------------------------------------------------
# Name lookup: injection safety, alias resolution, deterministic precedence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_alias(db):
    await family_service.create_family_member(
        db, name="Aaron Wilkinson", relationship="son", aliases=["Brad"]
    )
    found = await family_service.get_family_member_by_name(db, "brad")
    assert found is not None and found.name == "Aaron Wilkinson"


@pytest.mark.asyncio
async def test_lookup_with_apostrophe_is_safe(db):
    await family_service.create_family_member(db, name="Aaron Wilkinson", relationship="son")
    assert await family_service.get_family_member_by_name(db, "O'Brien") is None


@pytest.mark.asyncio
async def test_lookup_injection_attempt_finds_nothing_and_preserves_data(db):
    await family_service.create_family_member(
        db, name="Aaron Wilkinson", relationship="son", aliases=["Brad"]
    )
    # A boolean-injection payload must be treated as a literal name.
    assert await family_service.get_family_member_by_name(db, "' OR '1'='1") is None
    assert len(await family_service.list_family_members(db)) == 1


@pytest.mark.asyncio
async def test_lookup_treats_wildcards_literally(db):
    await family_service.create_family_member(db, name="Aaron Wilkinson", relationship="son")
    assert await family_service.get_family_member_by_name(db, "%") is None
    assert await family_service.get_family_member_by_name(db, "_") is None


@pytest.mark.asyncio
async def test_lookup_prefers_exact_match(db):
    await family_service.create_family_member(db, name="Anna Wilkinson", relationship="granddaughter")
    await family_service.create_family_member(db, name="Anna", relationship="daughter-in-law")
    found = await family_service.get_family_member_by_name(db, "Anna")
    assert found.name == "Anna"
