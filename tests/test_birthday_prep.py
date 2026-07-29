from datetime import date

import pytest

from app.scheduler.jobs import birthday_prep
from app.services import family_circle_service as circle
from app.services import family_service


def test_humanize():
    assert birthday_prep._humanize(14) == "2 weeks"
    assert birthday_prep._humanize(7) == "1 week"
    assert birthday_prep._humanize(10) == "10 days"


async def _make(db, name, **kw):
    return await family_service.create_family_member(db, name=name, relationship="granddaughter", gender="female", **kw)


@pytest.mark.asyncio
async def test_compose_without_ideas(db):
    bea = await _make(db, "Bea Test", birthday=date(2016, 9, 25))
    msg = await birthday_prep.compose_birthday_prep(db, bea, 14)
    assert "Bea" in msg
    assert "2 weeks" in msg
    assert "ask the family" in msg.lower()


@pytest.mark.asyncio
async def test_compose_with_ideas(db):
    aaron = await family_service.create_family_member(db, name="Aaron Dad", relationship="son", gender="male")
    bea = await _make(db, "Bea Two", birthday=date(2016, 9, 25))
    await circle.add_input(db, aaron.id, "gift_idea", "An animal encounter day", about_member_id=bea.id)

    msg = await birthday_prep.compose_birthday_prep(db, bea, 14)
    assert "animal encounter day" in msg
    assert "already shared" in msg.lower()


@pytest.mark.asyncio
async def test_get_inputs_about_filters_by_member(db):
    aaron = await family_service.create_family_member(db, name="Aaron Three", relationship="son", gender="male")
    bea = await _make(db, "Bea Three")
    other = await _make(db, "Zoe Three")
    await circle.add_input(db, aaron.id, "gift_idea", "for bea", about_member_id=bea.id)
    await circle.add_input(db, aaron.id, "gift_idea", "for zoe", about_member_id=other.id)

    bea_ideas = await circle.get_inputs_about(db, bea.id, kinds=["gift_idea"])
    assert len(bea_ideas) == 1
    assert bea_ideas[0].content == "for bea"
