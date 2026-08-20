import pytest
from datetime import date, timedelta

from app.services import family_service


@pytest.mark.asyncio
async def test_create_and_retrieve_family_member(db):
    member = await family_service.create_family_member(
        db,
        name="Wren",
        relationship="granddaughter",
        city="Nashville",
        state="TN",
        birthday=date(2016, 3, 15),
        grade_level="4th",
        interests=["dinosaurs", "art", "swimming"],
        personality_notes="Energetic, loves learning, gets excited about animals",
    )
    assert member.id is not None

    found = await family_service.get_family_member_by_name(db, "Wren")
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
        db, name="Dominic Rivers", relationship="son", aliases=["Nico"]
    )
    found = await family_service.get_family_member_by_name(db, "nico")
    assert found is not None and found.name == "Dominic Rivers"


@pytest.mark.asyncio
async def test_lookup_with_apostrophe_is_safe(db):
    await family_service.create_family_member(db, name="Dominic Rivers", relationship="son")
    assert await family_service.get_family_member_by_name(db, "O'Brien") is None


@pytest.mark.asyncio
async def test_lookup_injection_attempt_finds_nothing_and_preserves_data(db):
    await family_service.create_family_member(
        db, name="Dominic Rivers", relationship="son", aliases=["Nico"]
    )
    # A boolean-injection payload must be treated as a literal name.
    assert await family_service.get_family_member_by_name(db, "' OR '1'='1") is None
    assert len(await family_service.list_family_members(db)) == 1


@pytest.mark.asyncio
async def test_lookup_treats_wildcards_literally(db):
    await family_service.create_family_member(db, name="Dominic Rivers", relationship="son")
    assert await family_service.get_family_member_by_name(db, "%") is None
    assert await family_service.get_family_member_by_name(db, "_") is None


@pytest.mark.asyncio
async def test_lookup_prefers_exact_match(db):
    await family_service.create_family_member(db, name="Anna Rivers", relationship="granddaughter")
    await family_service.create_family_member(db, name="Anna", relationship="daughter-in-law")
    found = await family_service.get_family_member_by_name(db, "Anna")
    assert found.name == "Anna"


# ---------------------------------------------------------------------------
# Annual events used to vanish the day after they passed, while the tool told
# Cordia they recur.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_annual_event_survives_its_date(db):
    last_week = date.today() - timedelta(days=7)
    await family_service.create_family_event(
        db, title="Family Reunion", event_type="gathering",
        event_date=last_week, recurrence="annual",
    )
    events = await family_service.list_upcoming_events(db, days_ahead=400)
    assert [e.title for e in events] == ["Family Reunion"]


@pytest.mark.asyncio
async def test_one_time_event_does_not_come_back(db):
    await family_service.create_family_event(
        db, title="Dentist", event_type="appointment",
        event_date=date.today() - timedelta(days=7), recurrence="one_time",
    )
    events = await family_service.list_upcoming_events(db, days_ahead=400)
    assert events == []


@pytest.mark.asyncio
async def test_upcoming_one_time_event_still_listed(db):
    await family_service.create_family_event(
        db, title="School play", event_type="school_event",
        event_date=date.today() + timedelta(days=10), recurrence="one_time",
    )
    events = await family_service.list_upcoming_events(db, days_ahead=30)
    assert [e.title for e in events] == ["School play"]


@pytest.mark.asyncio
async def test_exact_alias_beats_a_substring_name_match(db):
    """The prompt no longer carries the name mapping, so this lookup is the only
    thing that resolves a former name. An alias hit used to land in the lowest
    ranking tier and lose to any member whose name merely contained it."""
    await family_service.create_family_member(
        db, name="Nicolas Rivers", relationship="in-law",
    )
    await family_service.create_family_member(
        db, name="Dominic Rivers", relationship="son", aliases=["Nico"],
    )
    found = await family_service.get_family_member_by_name(db, "Nico")
    assert found.name == "Dominic Rivers"


@pytest.mark.asyncio
async def test_exact_name_still_beats_an_alias(db):
    await family_service.create_family_member(
        db, name="Dominic Rivers", relationship="son", aliases=["Theo"],
    )
    await family_service.create_family_member(
        db, name="Theo", relationship="grandson",
    )
    found = await family_service.get_family_member_by_name(db, "Theo")
    assert found.name == "Theo"
