"""The audit has to be reachable by the person who needs it, and gated.

It began as a script, which meant a terminal, Python and a checked-out repo —
so the person it was built for could not open it. It is a dashboard page now,
behind the same login as everything else, and both front doors read the same
judgement from app/services/conversation_audit so they cannot disagree about
the same conversation.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import conversation_audit as audit


def _msgs(*pairs, start=None):
    """(role, text, minutes_from_start) → the row shape the audit reads."""
    t0 = start or datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    return [{"role": r, "content": c, "created": t0 + timedelta(minutes=m)}
            for r, c, m in pairs]


# --- reading the reply -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("US Open at Flushing Meadows - ", "CUT OFF mid-thought"),
    ("Checked live - best thing going today, Tom:", "CUT OFF mid-thought"),
    ("Something went wrong on my end. Please try again in a moment.", "DIED — error reply"),
    ("I'm on it - give me a moment and ask me again if you don't hear back.",
     "FALLBACK — model produced no text"),
    ("Sent - the full plan is in your inbox.", "PROMISED a delivery"),
    ("Here are three options under $600. Want me to hold one?", "ok"),
])
def test_replies_are_classified(text, expected):
    assert audit.classify_reply(text) == expected


# --- reading the ask ---------------------------------------------------------

def test_an_open_ended_ask_carries_nothing():
    prof = audit.profile_ask("Can you suggest something to do?", "", "")

    assert prof["open_ended"]
    assert prof["carried"] == []


def test_a_specific_ask_is_recognised_as_specific():
    prof = audit.profile_ask(
        "Can you find flights to Naples for October 12 for 4 people under $600?", "", "")

    assert not prof["open_ended"]
    assert {"date", "number", "budget"} <= set(prof["carried"])


def test_a_non_answer_to_a_question_is_caught():
    """Cord asked; she moved past it. The one signal that is really about
    prompting rather than about a bug."""
    prof = audit.profile_ask("ok", "Want me to hold one?", "")

    assert prof["ignored_cord"]
    assert not prof["answered_cord"]


def test_pushback_survives_the_question_mark():
    """A trailing "?" defeats a naive word boundary, and "hello?" is exactly
    what she types when nothing arrived."""
    for text in ("hello?", "??", "Anything else?", "didn't get it"):
        assert audit.profile_ask(text, "", "")["pushing_back"], text


def test_an_ordinary_ask_is_not_pushback():
    assert not audit.profile_ask("Can you find flights to Naples?", "", "")["pushing_back"]


# --- the exchange, and what came next ----------------------------------------

def test_her_message_is_paired_with_the_reply_it_drew():
    ex = audit.build_exchanges(_msgs(
        ("user", "In New York this afternoon. Can you suggest something to do?", 0),
        ("assistant", "US Open at Flushing Meadows - ", 1),
        ("user", "Anything else?", 2),
        ("assistant", "Here are three: the Met, the High Line, Bluestone.", 3),
    ))

    assert len(ex) == 2
    assert ex[0]["her"].startswith("In New York")
    assert ex[0]["cord"] == "US Open at Flushing Meadows - "
    assert ex[0]["verdict"] == "CUT OFF mid-thought"


def test_pushing_back_is_reported_against_the_previous_answer():
    ex = audit.build_exchanges(_msgs(
        ("user", "Suggest something to do", 0),
        ("assistant", "A few good ones:", 1),
        ("user", "hello?", 30),
    ))

    assert ex[0]["signal_kind"] == "pushed back — the answer did not land"


def test_a_long_silence_reads_as_going_quiet_not_moving_on():
    ex = audit.build_exchanges(_msgs(
        ("user", "Plan a trip to St Thomas", 0),
        ("assistant", "Here is a plan for St Thomas with flights and a taxi.", 1),
        ("user", "Different question about the Naples house", 3000),
    ))

    assert ex[0]["signal_kind"] == "went quiet"


def test_an_ask_with_no_reply_at_all_is_visible():
    ex = audit.build_exchanges(_msgs(("user", "Are you there?", 0)))

    assert ex[0]["verdict"] == "NO REPLY AT ALL"


def test_a_reply_to_nobody_does_not_invent_an_exchange():
    """Proactive sends — the morning brief, a price alert — are assistant rows
    with no ask in front of them."""
    ex = audit.build_exchanges(_msgs(("assistant", "Morning brief: nothing today.", 0)))

    assert ex == []


# --- the profile -------------------------------------------------------------

def test_the_profile_counts_behaviour_rather_than_impressions():
    s = audit.summarise(audit.build_exchanges(_msgs(
        ("user", "Suggest something", 0),
        ("assistant", "A few good ones:", 1),
        ("user", "hello?", 30),
        ("assistant", "Sorry - the Met, the High Line, and Bluestone Lane.", 31),
    )))

    assert s["exchanges"] == 2
    assert s["pct"]["pushing_back"] > 0
    assert s["unsound"] >= 1


def test_it_says_so_when_cord_never_asked_anything():
    s = audit.summarise(audit.build_exchanges(_msgs(
        ("user", "Suggest something", 0),
        ("assistant", "The Met is open until five.", 1),
    )))

    assert s["cord_never_asked"] is True


def test_an_empty_conversation_does_not_divide_by_zero():
    s = audit.summarise([])

    assert s["exchanges"] == 0
    assert s["median_words"] == 0


# --- the page ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_page_refuses_without_a_session(client):
    r = await client.get("/health/conversations")

    assert r.status_code == 401
    assert "password" in r.text.lower()


@pytest.mark.asyncio
async def test_the_page_never_leaks_a_transcript_to_a_stranger(client):
    """It renders somebody's private correspondence. The gate is the feature."""
    r = await client.get("/health/conversations")

    assert "exchanges" not in r.text.lower()


# --- the turns that did actual work ------------------------------------------

def _tool_turn(t0):
    """What a turn that used tools actually writes: the answer is three rows on."""
    from datetime import timedelta
    return [
        {"role": "user", "content": "A gift for Amber?", "created": t0},
        {"role": "assistant",
         "content": '[{"type":"tool_use","id":"t1","name":"recall_memory","input":{}}]',
         "created": t0 + timedelta(seconds=5)},
        {"role": "tool",
         "content": '[{"type":"tool_result","tool_use_id":"t1","content":"..."}]',
         "created": t0 + timedelta(seconds=6)},
        {"role": "assistant",
         "content": '[{"type":"text","text":"Amber loves pottery - three ideas."}]',
         "created": t0 + timedelta(seconds=20)},
    ]


def test_a_tool_using_turn_is_not_reported_as_silence():
    """The bug that hid Cordia's real answers. A turn that used tools writes
    assistant[tool_use] → tool[results] → assistant[the answer]; stopping at
    the first row called every turn that did work "NO REPLY AT ALL"."""
    t0 = datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)

    ex = audit.build_exchanges(_tool_turn(t0))[0]

    assert ex["verdict"] == "ok"
    assert ex["cord"] == "Amber loves pottery - three ideas."
    assert ex["rounds"] == 1
    assert ex["took"] == "20s later"


def test_tool_results_are_never_mistaken_for_something_she_said():
    ex = audit.build_exchanges(_tool_turn(
        datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)))

    assert len(ex) == 1
    assert ex[0]["her"] == "A gift for Amber?"


def test_the_last_answer_wins_not_the_holding_note():
    """Only the final text is sent to her; the earlier one was never delivered."""
    t0 = datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)
    ex = audit.build_exchanges(_msgs(
        ("user", "Find me flights", 0),
        ("assistant", "Let me check that for you.", 1),
        ("assistant", "Three options, cheapest is $214 on the 20th.", 3),
        ("user", "thanks", 5),
    ), )[0]

    assert ex["cord"] == "Three options, cheapest is $214 on the 20th."


def test_a_turn_that_only_called_tools_and_never_spoke_is_still_silence():
    t0 = datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)
    rows = _tool_turn(t0)[:3]  # drop the answering row

    assert audit.build_exchanges(rows)[0]["verdict"] == "NO REPLY AT ALL"


# --- written versus actually delivered ---------------------------------------

def _hi(t0):
    return _msgs(("user", "Hi", 0),
                 ("assistant", "Hi there! What can I help you with today?", 1),
                 start=t0)


def test_a_reply_that_never_left_the_building_is_marked():
    """She texted "Hi" and got nothing, but the reply was composed and stored.
    Only a real send writes an sms_out row, so its absence is the evidence."""
    t0 = datetime(2026, 8, 20, 19, 27, tzinfo=timezone.utc)
    ex = audit.build_exchanges(_hi(t0))
    audit.mark_delivery(ex, [t0 + timedelta(days=3)])

    assert ex[0]["verdict"] == "NEVER SENT — written but not delivered"


def test_a_reply_with_a_send_beside_it_is_left_alone():
    t0 = datetime(2026, 8, 20, 19, 27, tzinfo=timezone.utc)
    ex = audit.build_exchanges(_hi(t0))
    audit.mark_delivery(ex, [t0 + timedelta(seconds=4)])

    assert ex[0]["verdict"] == "ok"


def test_no_ledger_at_all_accuses_nobody():
    """An email thread, or a window before billing was recorded. Absence of a
    ledger is not evidence that nothing was delivered."""
    t0 = datetime(2026, 8, 20, 19, 27, tzinfo=timezone.utc)
    ex = audit.build_exchanges(_hi(t0))
    audit.mark_delivery(ex, [])

    assert ex[0]["verdict"] == "ok"
