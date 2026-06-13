import pytest

from app.services import family_circle_service as circle
from app.services import family_service


async def _make_member(db, name, phone, relationship="son"):
    return await family_service.create_family_member(
        db, name=name, relationship=relationship, gender="male", phone=phone
    )


@pytest.mark.asyncio
async def test_resolve_by_phone_and_access_gating(db):
    await _make_member(db, "Aaron Test", "6158539483")

    # Twilio sends +1 prefix; resolver normalizes to last 10 digits
    m = await circle.resolve_member_by_phone(db, "+16158539483")
    assert m is not None and m.name == "Aaron Test"

    # Access is denied by default
    assert m.has_circle_access is False

    # Granting flips the flag
    granted = await circle.grant_circle_access(db, "Aaron Test")
    assert granted.has_circle_access is True


@pytest.mark.asyncio
async def test_unknown_phone_resolves_to_none(db):
    m = await circle.resolve_member_by_phone(db, "+19998887777")
    assert m is None


@pytest.mark.asyncio
async def test_input_surfacing_flow(db):
    aaron = await _make_member(db, "Aaron Two", "6150000001")

    item = await circle.add_input(db, aaron.id, "gift_idea", "An animal encounter day")
    pending = await circle.get_unsurfaced_inputs(db)
    assert len(pending) == 1

    await circle.mark_inputs_surfaced(db, [item.id])
    assert len(await circle.get_unsurfaced_inputs(db)) == 0


@pytest.mark.asyncio
async def test_request_visible_then_fulfilled(db):
    aaron = await _make_member(db, "Aaron Three", "6150000002")
    await circle.grant_circle_access(db, "Aaron Three")

    req = await circle.create_request(db, "gift ideas for Bea")
    open_reqs = await circle.get_open_requests_for(db, aaron)
    assert any(r.prompt == "gift ideas for Bea" for r in open_reqs)

    await circle.fulfill_request(db, req.id)
    assert all(r.fulfilled for r in [req])


@pytest.mark.asyncio
async def test_shared_schedule_defaults_to_empty(db):
    # Nothing on Cordia's calendar is visible to family until she approves it
    assert len(await circle.get_shared_schedule(db)) == 0
