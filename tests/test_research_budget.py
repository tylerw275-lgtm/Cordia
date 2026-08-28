"""The research budget was the same size for every question.

"I hit my research limit before reading their prices" reached Cordia three
separate times — car services, then balloons — always on the half she cared
about most. Both caps were flat: eight searches and five page reads whether the
turn was "what's the weather" or "price four Nashville vendors from their own
sites".

Pricing four vendors costs a search each to find them and a fetch each to read
them, so the fifth page read was always going to be the wall. And a fetch is
free — only search is billed, at a cent — so the cap that hurt most cost
nothing to lift.
"""
import pytest

from app.config import settings
from app.prompts.prompt_profiles import get_profile
from app.prompts.system_prompt import build_system_prompt
from app.services.claude_service import _web_tools


def _uses(deep: bool) -> dict:
    tools = _web_tools("owner", get_profile(settings.claude_model), deep)
    return {t["name"]: t["max_uses"] for t in tools}


def test_deep_work_gets_a_bigger_budget_than_a_quick_question():
    quick, deep = _uses(False), _uses(True)

    assert deep["web_search"] > quick["web_search"]
    assert deep["web_fetch"] > quick["web_fetch"]


def test_a_deep_turn_can_price_a_field_of_vendors():
    """Four vendors, each found and then read from their own site, with room to
    follow a link that turns out to be the wrong page."""
    deep = _uses(True)

    assert deep["web_search"] >= 20
    assert deep["web_fetch"] >= 20


def test_reading_pages_is_never_the_tighter_cap():
    """Verifying from source is the discipline she asked for, and a fetch costs
    nothing per use — so it must never be the limit that stops the work."""
    for deep in (False, True):
        uses = _uses(deep)
        assert uses["web_fetch"] > uses["web_search"], f"deep={deep}"


def test_the_billed_one_stays_bounded():
    """Search is a cent each. A ceiling, not a budget — but still a ceiling."""
    assert _uses(True)["web_search"] <= 40
    assert settings.web_search_cost == 0.01
    assert settings.web_fetch_cost == 0.0


def test_a_quick_question_is_not_handed_a_research_budget():
    quick = _uses(False)

    assert quick["web_search"] <= 8


@pytest.mark.parametrize("role", ["family", "untrusted"])
def test_the_bigger_budget_does_not_leak_to_other_roles(role):
    assert _web_tools(role, get_profile(settings.claude_model), True) == []


def test_it_is_told_not_to_talk_about_limits():
    """"I hit my research limit" is a sentence about our plumbing."""
    text = " ".join(b["text"] for b in build_system_prompt())

    assert 'NEVER TELL HER YOU HIT A "RESEARCH LIMIT"' in text
    assert "Say what it costs her instead" in text
    assert "deliver everything you did verify rather than holding it back" in text
