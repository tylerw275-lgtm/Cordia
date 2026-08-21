"""Interview before answering.

Naples and NYC are examples of a *style* of ask, not the only two shapes. The
engine has to run a real interview for a request nobody anticipated — so most of
these tests are about the derived path, not the pre-written playbooks.
"""
import pytest
from sqlalchemy import select

from app.models.project import Project
from app.prompts import playbooks
from app.tools import project_tools as pt


async def _start(db, title, request, kind=None):
    kw = {"title": title, "request": request}
    if kind:
        kw["kind"] = kind
    return await pt.start_project_handler(db, **kw)


# --- routing ---------------------------------------------------------------

@pytest.mark.parametrize("request_text,expected", [
    ("what should I pack for the naples house", "place_setup"),
    ("find me a car service in nyc for thursday", "service_sourcing"),
    ("help me plan a comedy night", "event_planning"),
])
def test_known_shapes_route_to_a_playbook(request_text, expected):
    assert playbooks.match(request_text) == expected


@pytest.mark.parametrize("request_text", [
    "what's the best way to store wine in a humid basement",
    "should I refinance the office building",
    "how do I get Elijah interested in chess",
])
def test_unanticipated_asks_do_not_match_and_that_is_fine(request_text):
    """A miss costs nothing — the derived path is the normal case, not an error."""
    assert playbooks.match(request_text) is None


# --- the derived path (the one that has to generalise) ---------------------

@pytest.mark.asyncio
async def test_an_unanticipated_ask_gets_a_question_design_brief(db):
    result = await _start(db, "Wine storage", "best way to store wine in a humid basement")

    assert result["kind"] == "derived"
    assert "question_design_brief" in result
    assert "intake_questions" not in result
    # The dimensions that generalise are handed over, not left to recall.
    for axis, _ in playbooks.INTAKE_DIMENSIONS:
        assert axis in result["question_design_brief"]


@pytest.mark.asyncio
async def test_derived_questions_persist_and_become_the_interview(db):
    started = await _start(db, "Wine storage", "storing wine in a humid basement")
    pid = started["project_id"]

    saved = await pt.save_project_questions_handler(
        db, project_id=pid,
        questions=["How much wine, and how long are you keeping it?",
                   "Is the basement climate controlled at all?"],
    )
    assert saved["saved"] is True

    got = await pt.get_project_handler(db, project_id=pid)
    assert [q["question"] for q in got["brief"]] == saved["questions"]
    assert len(got["still_missing"]) == 2


@pytest.mark.asyncio
async def test_empty_derived_questions_are_refused(db):
    started = await _start(db, "Wine storage", "storing wine")
    out = await pt.save_project_questions_handler(
        db, project_id=started["project_id"], questions=["  ", ""]
    )
    assert out["saved"] is False and out["reason"] == "no_questions"


@pytest.mark.asyncio
async def test_saving_questions_twice_does_not_duplicate_them(db):
    started = await _start(db, "Wine storage", "storing wine")
    pid = started["project_id"]
    qs = ["How much wine?"]
    await pt.save_project_questions_handler(db, project_id=pid, questions=qs)
    await pt.save_project_questions_handler(db, project_id=pid, questions=qs)

    got = await pt.get_project_handler(db, project_id=pid)
    assert len(got["brief"]) == 1


# --- the interview itself --------------------------------------------------

@pytest.mark.asyncio
async def test_a_matched_ask_returns_questions_and_forbids_answering_yet(db):
    result = await _start(db, "Naples house", "what should I pack for the naples house")

    assert result["kind"] == "place_setup"
    assert len(result["intake_questions"]) >= 3
    assert result["answers_on_file"] == 0
    assert "do not answer the request yet" in result["next_step"].lower()
    # Research direction travels with the questions, so the answer is not just
    # her answers reformatted.
    assert result["research_checklist"]


@pytest.mark.asyncio
async def test_partial_answers_are_kept_and_only_the_rest_re_asked(db):
    """She answers three of five on Tuesday and the rest on Thursday. Re-asking
    what she already told you is the fastest way to feel like a robot."""
    started = await _start(db, "Naples house", "what should I pack for the naples house")
    pid = started["project_id"]
    first = started["intake_questions"][0]

    out = await pt.save_project_answers_handler(
        db, project_id=pid, answers=[{"question": first, "answer": "About 2,800 sq ft, a condo"}]
    )

    assert first not in out["still_missing"]
    assert len(out["still_missing"]) == len(started["intake_questions"]) - 1


@pytest.mark.asyncio
async def test_an_answer_matches_a_loosely_quoted_question(db):
    """The model rarely echoes a question back verbatim. A near-miss must update
    the existing question, not silently append a duplicate she gets re-asked."""
    started = await _start(db, "Naples house", "packing for the naples house")
    pid = started["project_id"]
    n = len(started["intake_questions"])

    await pt.save_project_answers_handler(
        db, project_id=pid,
        answers=[{"question": "How big is the place", "answer": "2,800 sq ft"}],
    )

    got = await pt.get_project_handler(db, project_id=pid)
    assert len(got["brief"]) == n, "a loose quote created a duplicate question"
    assert len(got["still_missing"]) == n - 1


@pytest.mark.asyncio
async def test_status_advances_only_once_the_interview_is_complete(db):
    started = await _start(db, "Naples house", "packing for the naples house")
    pid = started["project_id"]

    await pt.save_project_answers_handler(
        db, project_id=pid,
        answers=[{"question": started["intake_questions"][0], "answer": "2,800 sq ft"}],
    )
    assert (await pt.get_project_handler(db, project_id=pid))["status"] == "intake"

    await pt.save_project_answers_handler(
        db, project_id=pid,
        answers=[{"question": q, "answer": "whatever"} for q in started["intake_questions"][1:]],
    )
    assert (await pt.get_project_handler(db, project_id=pid))["status"] == "researching"


@pytest.mark.asyncio
async def test_a_project_survives_being_picked_up_later(db):
    """The whole reason this is a table and not conversation history."""
    started = await _start(db, "Naples house", "packing for the naples house")
    pid = started["project_id"]
    await pt.save_project_answers_handler(
        db, project_id=pid,
        answers=[{"question": started["intake_questions"][0], "answer": "2,800 sq ft, condo"}],
    )

    later = await pt.get_project_handler(db, project_id=pid)
    answered = [q for q in later["brief"] if q["answer"]]
    assert answered[0]["answer"] == "2,800 sq ft, condo"


# --- sourcing rules --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_finding_without_a_source_is_rejected(db):
    started = await _start(db, "Naples house", "packing for the naples house")
    out = await pt.save_project_findings_handler(
        db, project_id=started["project_id"],
        findings=[
            {"fact": "Rainy season runs June to October", "source_url": "https://weather.example/naples"},
            {"fact": "Everyone wears linen", "source_url": ""},
        ],
    )
    assert out["kept"] == 1
    assert out["rejected"] == ["Everyone wears linen"]


@pytest.mark.asyncio
async def test_a_quote_without_a_source_is_rejected(db):
    """An uncited price is worse than no price — she will act on it."""
    started = await _start(db, "NYC evening", "find me a car service in nyc")
    out = await pt.save_quote_options_handler(
        db, project_id=started["project_id"],
        options=[
            {"vendor": "Carey", "price": "$780", "source_url": "https://carey.example/rates"},
            {"vendor": "Guessed Limo", "price": "$600"},
        ],
    )
    assert out["kept"] == ["Carey"]
    assert out["rejected"] == ["Guessed Limo"]

    got = await pt.get_project_handler(db, project_id=started["project_id"])
    assert [q["vendor"] for q in got["quotes"]] == ["Carey"]


@pytest.mark.asyncio
async def test_quotes_carry_the_no_card_reminder(db):
    started = await _start(db, "NYC evening", "book a car service")
    out = await pt.save_quote_options_handler(
        db, project_id=started["project_id"],
        options=[{"vendor": "Carey", "price": "$780", "source_url": "https://x.example"}],
    )
    assert "card number" in out["reminder"].lower()


@pytest.mark.asyncio
async def test_multi_stop_domain_knowledge_reaches_the_model(db):
    """The bit that makes the answer better than one she'd get herself."""
    started = await _start(db, "NYC evening", "find me a car service for thursday night")
    assert "as directed" in (started.get("domain_notes") or "").lower()


# --- delivery --------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivering_records_the_result_and_the_gaps(db):
    started = await _start(db, "Naples house", "packing for the naples house")
    pid = started["project_id"]
    await pt.save_project_answers_handler(
        db, project_id=pid,
        answers=[{"question": started["intake_questions"][0], "answer": "2,800 sq ft"}],
    )

    out = await pt.deliver_project_handler(db, project_id=pid, deliverable="# The list\n...")

    assert out["delivered"] is True
    assert out["answered_questions"] == 1
    assert out["unanswered_questions"]
    # Unanswered questions became assumptions, and she is told which.
    assert "assumptions" in out["next_step"].lower()

    row = (await db.execute(select(Project))).scalars().one()
    assert row.status == "delivered"
    assert row.deliverable.startswith("# The list")


@pytest.mark.asyncio
async def test_unknown_project_ids_fail_cleanly(db):
    for handler, key in (
        (pt.get_project_handler, "found"),
        (pt.save_project_answers_handler, "saved"),
        (pt.deliver_project_handler, "delivered"),
    ):
        out = await handler(db, project_id="not-a-uuid", answers=[], deliverable="x")
        assert out[key] is False


@pytest.mark.asyncio
async def test_listing_shows_what_is_still_waiting_on_her(db):
    a = await _start(db, "Naples house", "packing for the naples house")
    await _start(db, "NYC evening", "find me a car service")
    await pt.save_project_answers_handler(
        db, project_id=a["project_id"],
        answers=[{"question": q, "answer": "x"} for q in a["intake_questions"]],
    )

    listed = await pt.list_projects_handler(db)
    assert listed["count"] == 2
    by_title = {p["title"]: p for p in listed["projects"]}
    assert by_title["Naples house"]["still_missing"] == []
    assert by_title["NYC evening"]["still_missing"]

    only_intake = await pt.list_projects_handler(db, status="intake")
    assert [p["title"] for p in only_intake["projects"]] == ["NYC evening"]
