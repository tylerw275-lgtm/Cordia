"""An inbound email should read like a document, not like a wall.

Two problems in one. html_to_text stripped every tag, so a deliverable sent as
headings, lists and links came back as an undifferentiated block — harder to
read and no cheaper. And the body cap was 50,000 characters against a 48,000
character history window, so a single forwarded thread could evict an entire
conversation. Tool results are capped at 3,000 on replay; a user message was
capped at nothing.

Converting to markdown is the better version of a cap: it removes bulk that
carries no meaning rather than cutting off the end.
"""
import pytest

from app.config import settings
from app.services.email_inbound import (
    describe_message, html_to_markdown, readable_body, strip_quoted,
)

DELIVERABLE = """<div>
<h1>NYC Day Trip</h1>
<h2>THE GAME</h2>
<p><strong>Yankees vs. Astros</strong></p>
<p>First pitch: <strong>7:05pm ET</strong></p>
<ul><li>Arrive LGA ~11:34am</li><li>Early dinner 5:00pm</li></ul>
<p><a href="http://blackcarservice.nyc">blackcarservice.nyc</a></p>
<hr>
<blockquote>an earlier message</blockquote>
</div>"""


@pytest.mark.parametrize("fragment,expected", [
    ("<h1>Title</h1>", "# Title"),
    ("<h2>Section</h2>", "## Section"),
    ("<h3>Sub</h3>", "### Sub"),
    ("<strong>bold</strong>", "**bold**"),
    ("<b>bold</b>", "**bold**"),
    ("<em>italic</em>", "*italic*"),
    ("<li>item</li>", "- item"),
    ('<a href="http://x.co">link</a>', "[link](http://x.co)"),
    ("<blockquote>quoted</blockquote>", "> quoted"),
    ("<hr>", "---"),
])
def test_structure_the_sender_gave_it_survives(fragment, expected):
    assert expected in html_to_markdown(fragment)


def test_a_real_deliverable_keeps_its_shape():
    out = html_to_markdown(DELIVERABLE)
    assert "# NYC Day Trip" in out
    assert "## THE GAME" in out
    assert "- Arrive LGA ~11:34am" in out
    assert "[blackcarservice.nyc](http://blackcarservice.nyc)" in out
    assert "<" not in out and ">" not in out.replace("> an earlier", "")


def test_scripts_and_styles_do_not_become_body_text():
    html = "<style>.x{color:red}</style><script>alert(1)</script><p>Hello</p>"
    out = html_to_markdown(html)
    assert "color:red" not in out and "alert" not in out
    assert "Hello" in out


def test_entities_are_decoded():
    out = html_to_markdown("<p>Tom &amp; Cordia &mdash; 5&nbsp;pm &hellip;</p>")
    assert "Tom & Cordia - 5 pm ..." in out


def test_blank_runs_are_collapsed():
    """Mail clients emit a lot of empty divs, and each one used to be a line."""
    out = html_to_markdown("<p>one</p><div></div><div></div><div></div><p>two</p>")
    assert "\n\n\n" not in out


def test_an_empty_document_is_empty_not_an_exception():
    for empty in ("", None):
        assert html_to_markdown(empty) == ""


# --- what actually enters the conversation ----------------------------------

def test_the_html_part_is_preferred_because_that_is_where_structure_lives():
    body = readable_body("Options\nDelta 1422", "<h2>Options</h2><ul><li>Delta 1422</li></ul>")
    assert "## Options" in body


def test_plain_text_is_used_when_there_is_no_html():
    assert readable_body("just text", None) == "just text"


def test_plain_text_is_used_when_the_html_renders_to_nothing():
    """An HTML part that is all markup leaves the message unreadable otherwise."""
    assert readable_body("the real content", "<div><span></span></div>") == "the real content"


def test_one_email_can_no_longer_be_bigger_than_the_whole_window():
    """The bug: 50,000 characters against a 48,000 character history window."""
    body = readable_body("x" * 200_000, None)
    assert len(body) < settings.history_max_chars
    assert settings.inbound_email_max_chars < settings.history_max_chars


def test_a_truncated_message_says_so():
    """So the model knows it is reading a fragment rather than assuming the
    message ended there."""
    body = readable_body("x" * 200_000, None)
    assert "more characters not shown" in body


def test_a_normal_email_is_not_touched():
    assert readable_body("Book the earlier one.", None) == "Book the earlier one."


# --- the header that makes a turn self-describing ---------------------------

def test_the_stored_turn_says_who_it_came_from_and_about_what():
    out = describe_message("tyler@ai-genpartners.com", "Re: flights", "Book it.")
    assert "tyler@ai-genpartners.com" in out
    assert "Re: flights" in out
    assert out.endswith("Book it.")


def test_a_missing_subject_does_not_produce_a_dangling_label():
    out = describe_message("tyler@ai-genpartners.com", "", "Book it.")
    assert "subject:" not in out
    assert "tyler@ai-genpartners.com" in out


# --- quoting still gets stripped --------------------------------------------

def test_quoted_history_is_still_removed_before_any_of_this():
    reply = "Book the earlier one.\n\nOn Fri, Aug 21 Tyler wrote:\n> here are the options\n"
    assert strip_quoted(reply).strip() == "Book the earlier one."


def test_a_paragraph_breaks_but_a_gmail_line_div_does_not():
    """Gmail wraps every single line in its own div. Treating those as
    paragraphs double-spaces an entire message."""
    assert html_to_markdown("<p>one</p><p>two</p>") == "one\n\ntwo"
    assert html_to_markdown("<div>one</div><div>two</div>") == "one\ntwo"
