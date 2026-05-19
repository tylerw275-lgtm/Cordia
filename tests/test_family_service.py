import pytest
from datetime import date

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
        event_date=date(2026, 7, 4),
        recurrence="annual",
    )
    events = await family_service.list_upcoming_events(db, days_ahead=365)
    assert any(e.id == event.id for e in events)
