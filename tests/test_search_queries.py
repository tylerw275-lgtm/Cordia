"""Their message is not the search query.

Cordia, Tom and Karie ask in one line — "in New York this afternoon, suggest
something to do" — and searching those words returns national listicles. The
prompt said when to search and how to source what came back, but nothing about
how to build the query, so the model was left to hand its raw input to the
search tool.

These assert the guidance is present and, more usefully, that it cannot reach a
role with no web tools — where it would contradict "you have NO web access" and
put two opposite instructions in one turn.
"""
import pytest

from app.prompts.system_prompt import (
    build_family_system_prompt,
    build_system_prompt,
    build_untrusted_system_prompt,
)
from app.services.claude_service import _WEB_RESEARCH_ROLES, _web_tools
from app.prompts.prompt_profiles import get_profile
from app.config import settings


def _owner_text() -> str:
    return " ".join(b["text"] for b in build_system_prompt())


MARKERS = (
    "BUILD THE SEARCH FROM WHAT THEY MEAN",
    "ONE QUESTION PER QUERY",
    "PUT THE SPECIFICS IN",
    "TIME-BOUND ANYTHING THAT MOVES",
    "GO TO THE SOURCE, THEN READ IT",
    "IF THE FIRST PASS IS THIN",
)


# --- present for the people who can actually search ---------------------------

@pytest.mark.parametrize("marker", MARKERS)
def test_the_owner_prompt_carries_the_query_guidance(marker):
    assert marker in _owner_text()


def test_it_says_to_derive_the_query_rather_than_echo_the_message():
    text = _owner_text()

    assert "Their message is not the query" in text
    assert "Their words tell you the goal; you write the query." in text


def test_it_does_not_send_them_searching_for_taste_or_situation():
    """That is the interview's job. Search is for facts about the world."""
    text = _owner_text()

    assert "do not search for their situation or taste" in text
    assert "Search is for facts about the world." in text


def test_reading_the_page_is_required_not_the_snippet():
    assert "open the venue's own page and take it from there" in _owner_text()


# --- absent everywhere it would be a lie -------------------------------------

@pytest.mark.parametrize("marker", MARKERS)
def test_the_family_prompt_never_gets_search_guidance(marker):
    """Family turns carry no web tools. Telling them how to search would sit
    directly against the "you have NO web access" line in the same prompt."""
    assert marker not in build_family_system_prompt("Tom")[0]["text"]


@pytest.mark.parametrize("marker", MARKERS)
def test_the_untrusted_prompt_never_gets_search_guidance(marker):
    assert marker not in build_untrusted_system_prompt()[0]["text"]


def test_family_is_still_told_plainly_that_it_cannot_look_anything_up():
    """The other half of the same contradiction — assert both sides together so
    one cannot be edited out from under the other."""
    text = build_family_system_prompt("Tom")[0]["text"]

    assert "You have NO web access here" in text
    assert 'Never say "checked live"' in text


def test_only_roles_with_web_tools_are_told_how_to_search():
    """The guarantee behind both halves: the prompt that describes searching
    goes to exactly the roles that have a search tool."""
    profile = get_profile(settings.claude_model)

    assert _WEB_RESEARCH_ROLES == ("owner",)
    for role in ("family", "untrusted"):
        assert _web_tools(role, profile) == []
    assert _web_tools("owner", profile), "owner must actually have the tool"
