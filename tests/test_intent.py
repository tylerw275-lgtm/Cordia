"""What a message is about, decided once.

There were three tables. `CONTEXT_KEYWORDS` chose the module brief,
`_DEEP_WORK_KEYWORDS` chose the token budget and the holding note, and
`_MATCH_HINTS` chose the interview playbook — all matched by naive substring,
with six words appearing in more than one of them.

Two kinds of failure came out of that, and both are pinned here. Substring
matching on short words fired constantly on ordinary text, and every false
positive on the deep-work table quietly quadrupled the budget. And the same
subject could be classified differently by two tables at once: event planning
had a brief saying "build the full plan" and a playbook saying "do not answer
yet", and both could reach the model in the same turn.
"""
import pytest

from app.prompts import intent, playbooks
from app.prompts.system_prompt import MODULE_CONTEXTS


# --- the substring bugs, by name --------------------------------------------

@pytest.mark.parametrize("message,trap", [
    ("I'll be at a different address", "rent inside different"),
    ("that's not what my parent said", "rent inside parent"),
    ("the current plan is fine", "rent inside current"),
    ("send me the camera roll", "cam inside camera"),
    ("she became a partner last year", "cam inside became"),
    ("I'm taking a shower", "show inside shower"),
    ("saw it on Facebook", "book inside Facebook"),
])
def test_a_keyword_no_longer_matches_the_inside_of_a_word(message, trap):
    """"I'll be at a different address" used to inject the sixty-line
    commercial-landlord brief."""
    assert intent.detect_context(message) != "lease_review", trap


def test_an_ordinary_message_does_not_buy_the_deep_budget():
    """Every false positive here silently set max_tokens to 8192 and effort to
    high — a direct cost multiplier on a message that said nothing."""
    for message in ("I'll be at a different address", "send me the camera roll",
                    "I'm taking a shower", "thanks!", "ok sounds good"):
        assert intent.is_deep_work(message) is False, message


# --- the classifications that must not drift --------------------------------

@pytest.mark.parametrize("message,expected", [
    ("what should I pack for the naples house", "place_setup"),
    ("find me a car service in nyc for thursday", "service_sourcing"),
    ("help me plan a comedy night", "event_planning"),
    ("can you review this lease renewal clause", "lease_review"),
    ("what time is my flight tomorrow", "trip_planning"),
    ("when is Bea's birthday", "family_coordination"),
])
def test_the_asks_she_actually_sends_are_classified(message, expected):
    assert intent.match(message).name == expected


@pytest.mark.parametrize("message", [
    "what's the best way to store wine in a humid basement",
    "should I refinance the office building",
    "how do I get Elijah interested in chess",
])
def test_an_unanticipated_ask_matches_nothing_and_that_is_fine(message):
    """A miss costs nothing — the project engine derives an interview from first
    principles, and that is the normal path rather than the exception."""
    assert intent.match(message) is None


def test_a_baby_shower_is_an_event_and_a_shower_is_not():
    """Word boundaries cannot tell these apart, so the keyword has to."""
    assert intent.match("throwing her a baby shower").name == "event_planning"
    assert intent.match("I'm taking a shower") is None


# --- one table means the three answers cannot disagree ----------------------

def test_every_context_a_message_can_produce_has_a_brief_to_show():
    """detect_context feeds straight into MODULE_CONTEXTS. A name with no entry
    is a silently dropped brief."""
    for row in intent.INTENTS:
        if row.context is not None:
            assert row.context in MODULE_CONTEXTS, row.name


def test_every_playbook_a_message_can_produce_actually_exists():
    for row in intent.INTENTS:
        if row.playbook is not None:
            assert playbooks.get(row.playbook) is not None, row.name


def test_the_playbook_router_and_the_intent_table_are_the_same_answer():
    """playbooks.match used to be a third table. Two tables meant a request
    could be an event to one and something else to the other."""
    for message in ("what should I pack for the naples house",
                    "help me plan a comedy night",
                    "find me a car service in nyc",
                    "how do I get Elijah interested in chess"):
        assert playbooks.match(message) == intent.playbook_for(message)


def test_the_budget_agrees_with_the_brief_it_ships_alongside():
    """lease_review carried the longest brief in the codebase on the SMALL
    budget, because it was in the context table and not the deep-work one, so
    the analysis very likely truncated."""
    assert intent.is_deep_work("review this lease", context_hint="lease_review")
    for row in intent.INTENTS:
        assert intent.is_deep_work("anything at all", context_hint=row.name) is row.deep


def test_the_event_brief_no_longer_contradicts_the_event_playbook():
    """The brief said "Build the full plan, not tips"; the playbook said "Do NOT
    answer the request yet". Both could be in context at once."""
    brief = MODULE_CONTEXTS["event_planning"].lower()
    assert "ask those first" in brief
    assert "not permission to skip the interview" in brief


# --- properties of the matcher itself ---------------------------------------

def test_matching_is_case_insensitive():
    assert intent.match("REVIEW THIS LEASE").name == "lease_review"


def test_a_phrase_matches_across_a_line_break():
    assert intent.match("find me a\ncar service").name == "service_sourcing"


def test_an_empty_message_matches_nothing():
    for empty in ("", None):
        assert intent.match(empty) is None
        assert intent.is_deep_work(empty) is False


def test_the_most_specific_intent_wins():
    """A lease question mentioning a trip is still a lease question."""
    assert intent.match("the tenant wants to travel before signing").name == "lease_review"


def test_deep_only_words_raise_the_budget_without_inventing_a_subject():
    """"Put together some options" is real work with no module brief to pick."""
    assert intent.is_deep_work("put together some options for me")
    assert intent.detect_context("put together some options for me") is None
