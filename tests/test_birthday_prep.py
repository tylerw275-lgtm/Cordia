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
    child = await _make(db, "Pia Test", birthday=date(2016, 9, 25))
    msg = await birthday_prep.compose_birthday_prep(db, child, 14)
    assert "Pia" in msg
    assert "2 weeks" in msg
    assert "ask the family" in msg.lower()


@pytest.mark.asyncio
async def test_compose_with_ideas(db):
    parent = await family_service.create_family_member(db, name="Dominic Dad", relationship="son", gender="male")
    child = await _make(db, "Pia Two", birthday=date(2016, 9, 25))
    await circle.add_input(db, parent.id, "gift_idea", "An animal encounter day", about_member_id=child.id)

    msg = await birthday_prep.compose_birthday_prep(db, child, 14)
    assert "animal encounter day" in msg
    assert "already shared" in msg.lower()


@pytest.mark.asyncio
async def test_get_inputs_about_filters_by_member(db):
    parent = await family_service.create_family_member(db, name="Dominic Three", relationship="son", gender="male")
    child = await _make(db, "Pia Three")
    other = await _make(db, "Wren Three")
    await circle.add_input(db, parent.id, "gift_idea", "for pia", about_member_id=child.id)
    await circle.add_input(db, parent.id, "gift_idea", "for wren", about_member_id=other.id)

    child_ideas = await circle.get_inputs_about(db, child.id, kinds=["gift_idea"])
    assert len(child_ideas) == 1
    assert child_ideas[0].content == "for pia"
