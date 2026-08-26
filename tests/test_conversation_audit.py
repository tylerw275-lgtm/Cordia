"""The audit has to be reachable by the person who needs it, and gated.

It began as a script, which meant a terminal, Python and a checked-out repo —
so the person it was built for could not open it. It is a dashboard page now,
behind the same login as everything else, and both front doors read the same
judgement from app/services/conversation_audit so they cannot disagree about
the same conversation.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.config import settings
from app.models.conversation import Conversation, Message
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
    Only a real send writes an sms_out row, so its absence is the evidence —
    but only where the ledger was live either side of it, which is what these
    two bracketing sends establish."""
    t0 = datetime(2026, 8, 20, 19, 27, tzinfo=timezone.utc)
    ex = audit.build_exchanges(_hi(t0))
    audit.mark_delivery(ex, [t0 - timedelta(days=1), t0 + timedelta(days=3)])

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


# --- the page with a session, which is where the code actually runs ----------

@pytest_asyncio.fixture
async def signed_in(client, monkeypatch):
    """A real session cookie. Without one the route returns at the gate and the
    whole database path — where the bugs live — never executes."""
    from app.api import dashboard

    monkeypatch.setattr(settings, "dashboard_password", "letmein", raising=False)
    client.cookies.set(dashboard._COOKIE, dashboard._issue_session())
    return client


@pytest.mark.asyncio
async def test_the_page_renders_a_real_conversation(db, signed_in):
    """It shipped raising NameError: sa_text was never imported in this route.

    Every existing test hit the 401 path, which returns before any database
    code runs, so the page was never actually executed by the suite.
    """
    conv = Conversation(phone_number="+16153005400")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    t0 = datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)
    db.add_all([
        Message(conversation_id=conv.id, role="user",
                content="A gift for Amber?", created_at=t0),
        Message(conversation_id=conv.id, role="assistant",
                content='[{"type":"tool_use","id":"t1","name":"recall_memory","input":{}}]',
                created_at=t0 + timedelta(seconds=5)),
        Message(conversation_id=conv.id, role="tool",
                content='[{"type":"tool_result","tool_use_id":"t1","content":"..."}]',
                created_at=t0 + timedelta(seconds=6)),
        Message(conversation_id=conv.id, role="assistant",
                content='[{"type":"text","text":"Amber loves pottery - three ideas."}]',
                created_at=t0 + timedelta(seconds=20)),
    ])
    await db.commit()

    r = await signed_in.get("/health/conversations")

    assert r.status_code == 200
    assert "Could not read the conversations" not in r.text
    assert "A gift for Amber?" in r.text
    # The whole point of the last fix: the answer behind the tool round shows.
    assert "Amber loves pottery" in r.text
    assert "1 tool round" in r.text


@pytest.mark.asyncio
async def test_an_empty_database_renders_rather_than_erroring(db, signed_in):
    r = await signed_in.get("/health/conversations")

    assert r.status_code == 200
    assert "Could not read the conversations" not in r.text


# --- whose thread is it --------------------------------------------------------

def test_the_signals_name_the_person_whose_thread_it_is():
    """It said "she" for everyone, which read as nonsense on Tom's thread. The
    audit covers everyone who texts Cord, not only the account holder."""
    msgs = _msgs(("user", "Suggest something", 0),
                 ("assistant", "A few good ones:", 1),
                 ("user", "Anything else?", 2))

    assert audit.build_exchanges(msgs, "Tom")[0]["signal"].startswith("Tom pushed back")
    assert audit.build_exchanges(msgs, "Cordia")[0]["signal"].startswith("Cordia pushed back")


def test_an_unknown_number_gets_they_rather_than_a_guess():
    msgs = _msgs(("user", "Suggest something", 0),
                 ("assistant", "A few good ones:", 1),
                 ("user", "Anything else?", 2))

    assert audit.build_exchanges(msgs)[0]["signal"].startswith("They pushed back")


def test_no_signal_line_assumes_a_gender():
    """Every branch of signal(), checked — not just the one the fixture hits."""
    msgs = _msgs(("user", "Plan a trip", 0),
                 ("assistant", "Here is a plan.", 1),
                 ("user", "no, I meant Naples", 4),
                 ("assistant", "Naples then.", 5),
                 ("user", "ok thanks very much indeed", 9),
                 ("assistant", "Anything else?", 10),
                 ("user", "no", 12),
                 ("assistant", "Right you are.", 13),
                 ("user", "Something unrelated entirely", 4000))

    for ex in audit.build_exchanges(msgs, "Tom"):
        lowered = ex["signal"].lower()
        assert " she " not in f" {lowered} "
        assert " her " not in f" {lowered} "
        assert not lowered.startswith(("she ", "her "))


@pytest.mark.asyncio
async def test_the_page_names_tom_on_toms_thread(db, signed_in):
    """End to end: the number resolves to a principal, and his name is what
    appears — as the bubble label, the heading, and in the signal line."""
    from app.models.authorized_user import AuthorizedUser

    db.add(AuthorizedUser(name="Tom Harrington", phone="+16157080001", is_owner=False))
    conv = Conversation(phone_number="+16157080001")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    db.add_all([
        Message(conversation_id=conv.id, role="user",
                content="Suggest something to do", created_at=t0),
        Message(conversation_id=conv.id, role="assistant",
                content="A few good ones:", created_at=t0 + timedelta(seconds=20)),
        Message(conversation_id=conv.id, role="user",
                content="Anything else?", created_at=t0 + timedelta(minutes=2)),
    ])
    await db.commit()

    r = await signed_in.get("/health/conversations")

    assert r.status_code == 200
    assert "Tom pushed back" in r.text
    assert "How Tom uses it" in r.text
    assert ">TOM<" in r.text
    # Nothing on the page calls Tom "she" or "her".
    for wrong in ("what she did next", "How she uses it", ">HER<",
                  "she answered", "she did not answer", "She pushed back"):
        assert wrong not in r.text


# --- a proactive send is not a reply -----------------------------------------

def test_the_morning_brief_is_not_counted_as_an_answer():
    """Her "$200" showed "[ok] · 23.8h later" — the reply was the next day's
    morning brief, written into the same conversation by
    record_assistant_message. She had in fact received nothing, which is the
    thing worth seeing and exactly what this hid."""
    t0 = datetime(2026, 8, 24, 13, 29, tzinfo=timezone.utc)
    ex = audit.build_exchanges([
        {"role": "user", "content": "$200", "created": t0},
        {"role": "assistant", "content": "Good morning! Here's your day (Wed Aug 26)",
         "created": t0 + timedelta(hours=23.8)},
    ], "Cordia")[0]

    assert ex["verdict"] == "NO REPLY AT ALL"
    assert ex["took"] == ""


def test_a_reply_inside_the_window_still_counts():
    t0 = datetime(2026, 8, 24, 13, 29, tzinfo=timezone.utc)
    ex = audit.build_exchanges([
        {"role": "user", "content": "$200", "created": t0},
        {"role": "assistant", "content": "Here are three at $200.",
         "created": t0 + timedelta(minutes=3)},
    ], "Cordia")[0]

    assert ex["verdict"] == "ok"


def test_deep_research_still_has_room_to_finish():
    """A long tool-using turn must not be mistaken for a proactive send."""
    t0 = datetime(2026, 8, 24, 13, 29, tzinfo=timezone.utc)
    ex = audit.build_exchanges([
        {"role": "user", "content": "Find me flights", "created": t0},
        {"role": "assistant", "content": "Three options, cheapest $214.",
         "created": t0 + timedelta(minutes=12)},
    ], "Cordia")[0]

    assert ex["verdict"] == "ok"


# --- one thread's name must not leak into another ----------------------------

@pytest.mark.asyncio
async def test_each_thread_is_labelled_with_its_own_person(db, signed_in):
    """Every bubble on the page read AARON — his was the last conversation
    processed, and `who` was computed in the loop that read the database and
    then read again in the loop that renders. The signal lines were right
    because they are baked in at build time; only the label was stale."""
    from app.models.authorized_user import AuthorizedUser

    db.add_all([
        AuthorizedUser(name="Cordia Harrington", phone="+16153005400", is_owner=True),
        AuthorizedUser(name="Tom Harrington", phone="+16157080001", is_owner=False),
    ])
    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    for phone, said in (("+16153005400", "A gift for Amber?"),
                        ("+16157080001", "Suggest something to do")):
        conv = Conversation(phone_number=phone)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        db.add_all([
            Message(conversation_id=conv.id, role="user", content=said, created_at=t0),
            Message(conversation_id=conv.id, role="assistant",
                    content="Here you go.", created_at=t0 + timedelta(seconds=10)),
        ])
    await db.commit()

    r = await signed_in.get("/health/conversations")

    assert r.status_code == 200
    assert ">CORDIA<" in r.text and ">TOM<" in r.text
    assert "How Cordia uses it" in r.text and "How Tom uses it" in r.text
    assert ">AARON<" not in r.text


@pytest.mark.asyncio
async def test_the_reply_line_does_not_gender_the_reader(db, signed_in):
    conv = Conversation(phone_number="+16157080001")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    db.add_all([
        Message(conversation_id=conv.id, role="user", content="hi", created_at=t0),
        Message(conversation_id=conv.id, role="assistant",
                content="What can I help with?", created_at=t0 + timedelta(seconds=3)),
    ])
    await db.commit()

    r = await signed_in.get("/health/conversations")

    assert "asked a question" in r.text
    assert "asked her something" not in r.text


# --- the ledger cannot speak to what came before it --------------------------

def test_replies_older_than_the_ledger_are_not_accused():
    """47 replies on one thread were branded undelivered. Billing was added
    partway through the project, so nothing before its first row has a send
    recorded — and those replies had plainly arrived and been answered."""
    old = datetime(2026, 7, 28, 16, 29, tzinfo=timezone.utc)
    ex = audit.build_exchanges([
        {"role": "user", "content": "Hello Cordia", "created": old},
        {"role": "assistant", "content": "Hello! I'm your assistant.",
         "created": old + timedelta(seconds=6)},
    ], "Tyler")
    audit.mark_delivery(ex, [datetime(2026, 8, 21, 19, 35, tzinfo=timezone.utc)])

    assert ex[0]["verdict"] == "ok"


def test_a_reply_after_the_ledger_started_is_still_judged():
    t0 = datetime(2026, 8, 22, 1, 44, tzinfo=timezone.utc)
    ex = audit.build_exchanges([
        {"role": "user", "content": "Find flights", "created": t0},
        {"role": "assistant", "content": "Three options.",
         "created": t0 + timedelta(seconds=30)},
    ], "Tyler")
    audit.mark_delivery(ex, [datetime(2026, 8, 21, 19, 35, tzinfo=timezone.utc)])

    assert ex[0]["verdict"] == "NEVER SENT — written but not delivered"


# --- filtering by person ------------------------------------------------------

@pytest_asyncio.fixture
async def three_threads(db):
    """Cordia and Tom are principals; the third number is nobody on file."""
    from app.models.authorized_user import AuthorizedUser

    db.add_all([
        AuthorizedUser(name="Cordia Harrington", phone="+16153005400", is_owner=True),
        AuthorizedUser(name="Tom Harrington", phone="+16157080001", is_owner=False),
    ])
    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    for phone, said in (("+16153005400", "A gift for Amber?"),
                        ("+16157080001", "Suggest something to do"),
                        ("+16158539483", "Trip to Quebec")):
        conv = Conversation(phone_number=phone)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        db.add_all([
            Message(conversation_id=conv.id, role="user", content=said, created_at=t0),
            Message(conversation_id=conv.id, role="assistant",
                    content="Here you go.", created_at=t0 + timedelta(seconds=10)),
        ])
    await db.commit()


@pytest.mark.asyncio
async def test_a_chip_appears_for_each_principal_plus_other(db, three_threads, signed_in):
    """Built from who is actually in the data — a hardcoded roster would list
    people with no conversations and go stale the moment one is added."""
    r = await signed_in.get("/health/conversations")

    assert 'href="/health/conversations?show=Cordia"' in r.text
    assert 'href="/health/conversations?show=Tom"' in r.text
    assert 'href="/health/conversations?show=Other"' in r.text
    assert ">All <" in r.text


@pytest.mark.asyncio
async def test_choosing_a_person_hides_everyone_else(db, three_threads, signed_in):
    r = await signed_in.get("/health/conversations?show=Cordia")

    assert "A gift for Amber?" in r.text
    assert "Suggest something to do" not in r.text
    assert "Trip to Quebec" not in r.text


@pytest.mark.asyncio
async def test_other_collects_everyone_who_is_not_a_principal(db, three_threads, signed_in):
    r = await signed_in.get("/health/conversations?show=Other")

    assert "Trip to Quebec" in r.text
    assert "A gift for Amber?" not in r.text


@pytest.mark.asyncio
async def test_no_filter_shows_all_of_them(db, three_threads, signed_in):
    r = await signed_in.get("/health/conversations")

    for said in ("A gift for Amber?", "Suggest something to do", "Trip to Quebec"):
        assert said in r.text


@pytest.mark.asyncio
async def test_the_chosen_chip_is_marked_active(db, three_threads, signed_in):
    r = await signed_in.get("/health/conversations?show=Tom")

    assert 'class="chip on" href="/health/conversations?show=Tom"' in r.text


@pytest.mark.asyncio
async def test_a_name_with_no_conversations_says_so_rather_than_looking_broken(
        db, three_threads, signed_in):
    r = await signed_in.get("/health/conversations?show=Nobody")

    assert r.status_code == 200
    assert "Nobody by that name has a conversation" in r.text


@pytest.mark.asyncio
async def test_the_filter_keeps_the_other_narrowing_you_had(db, three_threads, signed_in):
    """Choosing a person must not silently discard a ?since= you were using."""
    r = await signed_in.get("/health/conversations?since=2026-08-01")

    assert "show=Cordia&amp;since=2026-08-01" in r.text or \
           "show=Cordia&since=2026-08-01" in r.text
