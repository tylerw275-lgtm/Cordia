"""One conversation at a time.

Cordia asked what to pack, clarified "clothes", then added "it's a second home"
— three taps in quick succession. Every inbound webhook started its own turn
immediately, so all three ran in parallel, each loading history before the
others had written to it. She got two answers racing: one still asking the
questions the other had already answered.
"""
import asyncio

import pytest

from app.api import sms


@pytest.fixture(autouse=True)
def _clean():
    sms._INBOX.clear()
    sms._INBOX_LOCKS.clear()
    yield
    sms._INBOX.clear()
    sms._INBOX_LOCKS.clear()


def test_several_texts_fold_into_one_message():
    """"What to pack though. Clothes" then "It's a second home" is one thought
    split across two taps, not two questions."""
    body, media = sms._merge([("What to pack though. Clothes", []), ("It's a second home", [])])
    assert body == "What to pack though. Clothes\nIt's a second home"
    assert media == []


def test_merging_keeps_photos_from_every_message():
    _, media = sms._merge([("here's the lease", ["img1"]), ("and page two", ["img2"])])
    assert media == ["img1", "img2"]


def test_merging_drops_empty_bodies_without_losing_their_media():
    body, media = sms._merge([("", ["img1"]), ("look at this", [])])
    assert body == "look at this"
    assert media == ["img1"]


@pytest.mark.asyncio
async def test_turns_never_run_at_the_same_time(mocker):
    """The actual bug: two turns in flight at once, each blind to the other."""
    concurrent = 0
    peak = 0

    async def slow_turn(db, number, body, media):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1

    mocker.patch.object(sms, "_process_inbound", new=slow_turn)

    await asyncio.gather(*(
        sms._process_inbound_bg("+16157080002", text, [])
        for text in ("what should I pack", "clothes", "it's a second home")
    ))

    assert peak == 1, f"{peak} turns ran in parallel"


@pytest.mark.asyncio
async def test_a_message_arriving_mid_turn_joins_the_next_one(mocker):
    """Rather than starting a competing turn of its own."""
    seen = []

    async def turn(db, number, body, media):
        seen.append(body)
        await asyncio.sleep(0.02)

    mocker.patch.object(sms, "_process_inbound", new=turn)

    first = asyncio.create_task(sms._process_inbound_bg("+1615", "what should I pack", []))
    await asyncio.sleep(0.005)          # first turn is now running
    await asyncio.gather(
        first,
        sms._process_inbound_bg("+1615", "clothes", []),
        sms._process_inbound_bg("+1615", "it's a second home", []),
    )

    assert seen[0] == "what should I pack"
    assert "clothes\nit's a second home" in seen[1]
    assert len(seen) == 2, f"expected the two follow-ups to fold together: {seen}"


@pytest.mark.asyncio
async def test_no_message_is_dropped_under_a_burst(mocker):
    """The tempting optimisation — return early if someone else holds the lock —
    loses whatever arrived between that check and the release."""
    seen = []

    async def turn(db, number, body, media):
        seen.append(body)

    mocker.patch.object(sms, "_process_inbound", new=turn)

    await asyncio.gather(*(
        sms._process_inbound_bg("+1615", f"message {i}", []) for i in range(25)
    ))

    delivered = "\n".join(seen)
    for i in range(25):
        assert f"message {i}" in delivered, f"message {i} was dropped"


@pytest.mark.asyncio
async def test_two_people_are_not_serialised_against_each_other(mocker):
    """The lock is per conversation. One long turn must not hold up someone else."""
    active = set()
    overlapped = False

    async def turn(db, number, body, media):
        nonlocal overlapped
        active.add(number)
        await asyncio.sleep(0.02)
        if len(active) > 1:
            overlapped = True
        active.discard(number)

    mocker.patch.object(sms, "_process_inbound", new=turn)

    await asyncio.gather(
        sms._process_inbound_bg("+16157080002", "hello", []),
        sms._process_inbound_bg("+16155551234", "hello", []),
    )

    assert overlapped, "different people were queued behind each other"


@pytest.mark.asyncio
async def test_a_failed_turn_does_not_wedge_the_conversation(mocker):
    """A stuck lock would mean she is never answered again."""
    calls = []

    async def turn(db, number, body, media):
        calls.append(body)
        raise RuntimeError("boom")

    mocker.patch.object(sms, "_process_inbound", new=turn)

    await sms._process_inbound_bg("+1615", "first", [])
    await sms._process_inbound_bg("+1615", "second", [])

    assert calls == ["first", "second"]
    assert not sms._INBOX_LOCKS.get("+1615", asyncio.Lock()).locked()


@pytest.mark.asyncio
async def test_the_lock_table_stays_bounded(mocker):
    async def turn(db, number, body, media):
        pass

    mocker.patch.object(sms, "_process_inbound", new=turn)

    for i in range(sms._MAX_TRACKED_CONVERSATIONS + 100):
        await sms._process_inbound_bg(f"+1615555{i:04d}", "hey", [])

    assert len(sms._INBOX_LOCKS) <= sms._MAX_TRACKED_CONVERSATIONS
