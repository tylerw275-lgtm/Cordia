"""The holding notes Cord sends while a reply is still being written.

Two failures this guards against, both about how it *feels* rather than whether
it works: the same sentence every single time reads like a machine, and silence
during a minute of research reads like it stopped listening.
"""
import asyncio
import itertools

import pytest

from app.api import sms
from app.services.usage_service import sms_segments

ALL_NOTES = sms._WORKING_NOTES + sms._RESEARCH_NOTES + sms._STILL_WORKING_NOTES


# --- the notes themselves ---------------------------------------------------

@pytest.mark.parametrize("note", ALL_NOTES)
def test_every_note_is_plain_ascii(note):
    """Not a style rule, a cost one: one em dash or curly quote re-encodes the
    message as UCS-2 and drops the segment limit from 160 characters to 70,
    doubling the carrier cost of a note whose whole job is to be cheap."""
    assert note.isascii(), f"non-ASCII in {note!r} would double its cost"


@pytest.mark.parametrize("note", ALL_NOTES)
def test_every_note_is_a_single_segment(note):
    """Checked with the same function that bills it, so the cost property can't
    drift away from the pricing model."""
    assert sms_segments(note) == 1


def test_there_are_enough_phrasings_to_feel_varied():
    for pool in (sms._WORKING_NOTES, sms._RESEARCH_NOTES, sms._STILL_WORKING_NOTES):
        assert len(set(pool)) >= 3


# --- variation --------------------------------------------------------------

def test_the_same_line_never_lands_twice_in_a_row():
    """The original complaint. Random choice alone repeats often enough to be
    noticed, and a visible repeat is the exact thing this removes."""
    previous = None
    for _ in range(40):
        note = sms._working_note("+16155551234", "hey")
        assert note != previous
        previous = note


def test_notes_actually_vary_rather_than_alternating_between_two():
    seen = {sms._working_note("+16155559999", "hey") for _ in range(40)}
    assert len(seen) >= 3


def test_two_people_do_not_constrain_each_other():
    """The no-repeat rule is per person; one busy conversation must not narrow
    the choices in another."""
    for _ in range(20):
        sms._working_note("+16155550001", "hey")
    seen = {sms._working_note("+16155550002", "hey") for _ in range(20)}
    assert len(seen) >= 3


def test_the_last_note_table_stays_bounded():
    """It is keyed by phone number and lives for the process's lifetime."""
    for i in range(sms._LAST_NOTE_MAX + 50):
        sms._working_note(f"+1615555{i:04d}", "hey")
    assert len(sms._LAST_NOTE) <= sms._LAST_NOTE_MAX


# --- matched to the ask -----------------------------------------------------

def test_a_research_ask_says_what_is_actually_happening():
    """"Looking this up" reads as work. "One moment" reads as a stall."""
    seen = {sms._working_note("+16155551111", "find me a car service and price it")
            for _ in range(20)}
    assert seen <= set(sms._RESEARCH_NOTES)


def test_a_casual_message_gets_a_short_hold():
    seen = {sms._working_note("+16155552222", "hey how are you") for _ in range(20)}
    assert seen <= set(sms._WORKING_NOTES)


def test_follow_ups_say_still_regardless_of_the_ask():
    """A second "on it" reads like a stuck machine."""
    for body in ("hey", "plan a trip to naples"):
        note = sms._working_note("+16155553333", body, followup=True)
        assert note in sms._STILL_WORKING_NOTES


# --- the schedule -----------------------------------------------------------

def test_the_schedule_starts_quickly_then_backs_off():
    cumulative = list(itertools.accumulate(sms._NOTE_SCHEDULE))
    assert cumulative[0] <= 5, "the first note should land while she's still looking"
    assert cumulative == sorted(cumulative)
    assert sms._NOTE_SCHEDULE[1] > sms._NOTE_SCHEDULE[0], "later notes should back off"


def test_it_stops_rather_than_nagging_forever():
    """Every note is a billable segment, and a fourth is nagging rather than
    reassuring. The reply or the error arrives next either way."""
    assert len(sms._NOTE_SCHEDULE) <= 3


@pytest.mark.asyncio
async def test_a_fast_reply_gets_no_preamble_at_all(mocker):
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    mocker.patch.object(sms, "_NOTE_SCHEDULE", (0.05, 0.05))

    task = asyncio.create_task(sms._notify_if_slow("+16155551234", "hey"))
    await asyncio.sleep(0)          # the reply beat the timer
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not send.called


@pytest.mark.asyncio
async def test_a_long_turn_gets_a_follow_up_not_silence(mocker):
    """Going quiet through a minute of research reads worse than the repetition
    this change was meant to fix."""
    send = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    mocker.patch.object(sms, "_NOTE_SCHEDULE", (0.01, 0.01, 0.01))

    await sms._notify_if_slow("+16155554444", "plan a trip")

    bodies = [c.kwargs["body"] for c in send.await_args_list]
    assert len(bodies) == 3
    assert bodies[0] not in sms._STILL_WORKING_NOTES
    assert all(b in sms._STILL_WORKING_NOTES for b in bodies[1:])


@pytest.mark.asyncio
async def test_a_failed_note_does_not_kill_the_pending_reply(mocker):
    """The note is covering for an answer that is still coming. Losing that
    answer to a courtesy message would be a bad trade."""
    send = mocker.patch(
        "app.services.sms_service.send_sms",
        new=mocker.AsyncMock(side_effect=[RuntimeError("carrier hiccup"), None]),
    )
    mocker.patch.object(sms, "_NOTE_SCHEDULE", (0.01, 0.01))

    await sms._notify_if_slow("+16155555555", "hey")

    assert send.await_count == 2, "a failed note stopped the later ones"
