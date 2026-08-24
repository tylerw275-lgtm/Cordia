"""Most of every answer was being thrown away.

Tom's first message to Cord asked what to do in New York that afternoon. He got:

    "A few good ones for a late-August afternoon in the city:
     US Open at Flushing Meadows - "

and, a turn later:

    "Checked live - best thing going today, Tom:"

Both stop exactly where the list should start. `_extract_text` returned the
FIRST text block and stopped. That was right for a model that answered in one
block; Opus 5 thinks by default and interleaves thinking with text, so a reply
arrives as thinking / text / thinking / text and everything after the model's
first pause was generated, billed, and dropped on the floor.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.authorized_user import AuthorizedUser
from app.services import claude_service
from app.services.claude_service import _extract_text

OWNER = "+16157080002"


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _interleaved(*texts, stop_reason="end_turn"):
    """What Opus 5 actually returns: thinking between each piece of the answer."""
    content = []
    for text in texts:
        content.append(_blk(type="thinking", thinking="...", signature="sig"))
        content.append(_blk(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, usage=None, container=None,
                           content=content)


@pytest_asyncio.fixture
async def conversation(db):
    db.add(AuthorizedUser(name="Cordia Harrington", phone=OWNER, is_owner=True))
    await db.commit()
    return await claude_service.get_or_create_conversation(db, OWNER)


@pytest.fixture
def script(mocker):
    sent = []

    def _install(*responses):
        queue = list(responses)

        async def create(**kwargs):
            sent.append(kwargs)
            return queue.pop(0)

        mocker.patch.object(claude_service._client.messages, "create", new=create)
        return sent

    return _install


# --- the exact failure -------------------------------------------------------

def test_tom_gets_the_whole_answer():
    """His message, verbatim, and the half that never reached him."""
    response = _interleaved(
        "A few good ones for a late-August afternoon in the city:\n"
        "US Open at Flushing Meadows - ",
        "qualifying rounds are free this week. Also the High Line, and the Met.",
    )

    text = _extract_text(response.content)

    assert text.endswith("the High Line, and the Met.")
    assert "qualifying rounds are free" in text


def test_the_blocks_join_without_anything_inserted():
    """They are one stream the model already spaced. Anything added here lands
    mid-sentence — his first block ends on a trailing space for that reason."""
    response = _interleaved("US Open at Flushing Meadows - ", "free this week.")

    assert _extract_text(response.content) == "US Open at Flushing Meadows - free this week."


def test_a_single_block_is_unchanged():
    response = _interleaved("Just the one thing.")

    assert _extract_text(response.content) == "Just the one thing."


def test_thinking_never_leaks_into_the_reply():
    """She must never receive the model's reasoning."""
    response = _interleaved("The answer.")

    assert "..." not in _extract_text(response.content)


def test_no_text_at_all_is_still_empty():
    """The callers rely on falsiness to reach _FALLBACK_REPLY."""
    response = SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                               content=[_blk(type="thinking", thinking="...", signature="s")])

    assert _extract_text(response.content) == ""


def test_empty_blocks_are_skipped():
    response = SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                               content=[_blk(type="text", text=""),
                                        _blk(type="text", text="Real answer.")])

    assert _extract_text(response.content) == "Real answer."


# --- through the loop --------------------------------------------------------

@pytest.mark.asyncio
async def test_the_reply_she_receives_is_complete(db, conversation, script):
    script(_interleaved("Here are three options:\n1. The Met - ",
                        "open till 5.\n2. The High Line.\n3. Coffee at Bluestone."))

    reply = await claude_service.chat(db, conversation.id, "What should I do this afternoon?")

    assert "Bluestone" in reply
    assert reply.startswith("Here are three options:")
