"""Live web research, and the two failure modes that are silent without care.

`pause_turn` and server-tool errors both arrive as ordinary HTTP 200 responses.
Handled carelessly, the first truncates an answer mid-research and the second
reads as "the web had nothing to say" — in both cases Cordia gets a confident,
wrong answer with nothing logged.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.prompts.prompt_profiles import get_profile
from app.services import claude_service


def _block(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: {k: v for k, v in kw.items()}
    return b


def _text(t):
    return _block(type="text", text=t)


def _response(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


# --- tool assembly ---------------------------------------------------------

def test_owner_gets_search_and_fetch_on_a_modern_model():
    tools = claude_service._web_tools("owner", get_profile("claude-sonnet-4-6"))
    assert [t["name"] for t in tools] == ["web_search", "web_fetch"]
    assert tools[0]["type"] == "web_search_20260209"
    assert tools[0]["max_uses"] == settings.web_search_max_uses


def test_legacy_model_gets_the_basic_search_and_no_fetch():
    """web_fetch does not exist on older families — sending it would 400."""
    tools = claude_service._web_tools("owner", get_profile("claude-haiku-4-5"))
    assert [t["name"] for t in tools] == ["web_search"]
    assert tools[0]["type"] == "web_search_20250305"


@pytest.mark.parametrize("role", ["family", "untrusted"])
def test_family_and_untrusted_never_get_web_tools(role):
    """The untrusted role reads attacker-controllable content. Giving it a
    search tool would let that content go fetch more of itself."""
    assert claude_service._web_tools(role, get_profile("claude-sonnet-4-6")) == []


def test_flag_off_removes_the_tools(mocker):
    mocker.patch.object(settings, "enable_web_research", False)
    assert claude_service._web_tools("owner", get_profile("claude-sonnet-4-6")) == []


# --- server-tool errors ----------------------------------------------------

def test_error_object_is_recognised_as_a_failure():
    """A success carries a list; a failure carries an object. Indexing without
    checking reads the error as an empty result set."""
    assert claude_service._web_tool_error({"error_code": "max_uses_exceeded"}) == "max_uses_exceeded"


def test_result_list_is_not_a_failure():
    assert claude_service._web_tool_error([{"title": "a page"}]) is None


def test_collect_web_errors_finds_them_in_a_response():
    blocks = [
        _block(type="web_search_tool_result", content={"error_code": "unavailable"}),
        _text("here is what I found"),
    ]
    assert claude_service._collect_web_errors(blocks) == ["unavailable"]


# --- the loop --------------------------------------------------------------

@pytest.fixture
def _loop(db, mocker):
    mocker.patch.object(claude_service, "_persist_message", new=mocker.AsyncMock())
    mocker.patch.object(claude_service, "_load_history", new=mocker.AsyncMock(return_value=[]))
    mocker.patch.object(
        claude_service, "_build_owner_system",
        new=mocker.AsyncMock(return_value=[{"type": "text", "text": "sys"}]),
    )
    mocker.patch.object(claude_service, "get_tool_schemas", return_value=[])
    return mocker


@pytest.mark.asyncio
async def test_pause_turn_resumes_instead_of_truncating(db, _loop, mocker):
    """Without the resume branch this returned the half-finished text and
    called it an answer."""
    create = mocker.patch.object(
        claude_service._client.messages, "create", new=mocker.AsyncMock(side_effect=[
            _response("pause_turn", [_text("still looking...")]),
            _response("end_turn", [_text("Carey Limo quotes $780 for the evening.")]),
        ]),
    )
    out = await claude_service.chat(db, uuid.uuid4(), "find me a car service")

    assert out == "Carey Limo quotes $780 for the evening."
    assert create.await_count == 2
    # The resume re-sends the paused turn and adds NO new user message — the
    # API resumes from the trailing server_tool_use block on its own.
    resumed = create.await_args_list[1].kwargs["messages"]
    assert resumed[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_pause_resumes_are_capped(db, _loop, mocker):
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(return_value=_response("pause_turn", [_text("partial")])),
    )
    out = await claude_service.chat(db, uuid.uuid4(), "research something enormous")

    assert out == "partial"
    assert create.await_count == claude_service._MAX_PAUSE_RESUMES + 1


@pytest.mark.asyncio
async def test_failed_search_is_reported_not_passed_off_as_no_results(db, _loop, mocker):
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(return_value=_response("end_turn", [
            _block(type="web_search_tool_result", content={"error_code": "unavailable"}),
        ])),
    )
    out = await claude_service.chat(db, uuid.uuid4(), "what does a car service cost")

    assert "could not reach the web" in out.lower()
    assert "unavailable" in out


@pytest.mark.asyncio
async def test_a_search_that_errored_but_still_answered_keeps_the_answer(db, _loop, mocker):
    """One failed source among several must not discard a real answer."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(return_value=_response("end_turn", [
            _block(type="web_search_tool_result", content={"error_code": "unavailable"}),
            _text("Found it on the vendor's own site: $780."),
        ])),
    )
    out = await claude_service.chat(db, uuid.uuid4(), "what does a car service cost")

    assert out == "Found it on the vendor's own site: $780."


@pytest.mark.asyncio
async def test_web_tools_are_attached_to_the_request(db, _loop, mocker):
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(return_value=_response("end_turn", [_text("hi")])),
    )
    await claude_service.chat(db, uuid.uuid4(), "look up the weather in naples")

    sent = {t["name"] for t in create.await_args.kwargs["tools"]}
    assert {"web_search", "web_fetch"} <= sent


# --- resuming a paused turn needs the execution container -------------------
#
# The production failure, verbatim:
#   "container_id is required when there are pending tool uses generated by
#    code execution with tools."
#
# The current web tools run code execution internally to filter results before
# they reach the context window. Resuming a paused turn without carrying that
# container forward is a 400 — and the drop-the-web-tools fallback cannot
# rescue it, because the pending tool uses are already in the history. So the
# pause_turn handling added to avoid a silent truncation had been turning it
# into a hard failure instead.

import anthropic
import httpx


def _container_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={"error": {"message": "container_id"}})
    return anthropic.BadRequestError(
        "container_id is required when there are pending tool uses generated by "
        "code execution with tools.",
        response=response, body=None,
    )


def _paused(text="Found two so far", container_id="cont_abc123"):
    r = _response("pause_turn", [_text(text)])
    r.container = SimpleNamespace(id=container_id)
    return r


@pytest.mark.asyncio
async def test_resuming_carries_the_execution_container(db, _loop, mocker):
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[_paused(), _response("end_turn", [_text("All done.")])]),
    )

    out = await claude_service.chat(db, uuid.uuid4(), "find me a car service")

    assert out == "All done."
    assert "container" not in create.await_args_list[0].kwargs, "nothing to carry on the first call"
    assert create.await_args_list[1].kwargs.get("container") == "cont_abc123"


@pytest.mark.asyncio
async def test_a_response_without_a_container_still_resumes(db, _loop, mocker):
    """web_fetch alone may not spin one up."""
    plain = _response("pause_turn", [_text("still looking")])
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[plain, _response("end_turn", [_text("Done.")])]),
    )

    assert await claude_service.chat(db, uuid.uuid4(), "look something up") == "Done."
    assert "container" not in create.await_args_list[1].kwargs


@pytest.mark.asyncio
async def test_a_failed_resume_sends_the_partial_answer(db, _loop, mocker):
    """Exactly what happened in production. A half answer beats an apology, and
    dropping the web tools cannot help — the pending tool uses are already in
    the history."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[
            _paused("Carey quotes $780 so far"),
            _container_error(),
            _container_error(),   # the drop-web-tools retry fails identically
        ]),
    )

    out = await claude_service.chat(db, uuid.uuid4(), "find me a car service")

    assert out == "Carey quotes $780 so far"


@pytest.mark.asyncio
async def test_a_failure_with_nothing_partial_still_raises(db, _loop, mocker):
    """Only a real partial earns the softer path; otherwise the error is real
    and must surface."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=_container_error()),
    )

    with pytest.raises(anthropic.BadRequestError):
        await claude_service.chat(db, uuid.uuid4(), "find me a car service")


@pytest.mark.asyncio
async def test_the_container_persists_across_several_resumes(db, _loop, mocker):
    create = mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[
            _paused("one"), _paused("two"), _response("end_turn", [_text("Done.")]),
        ]),
    )

    assert await claude_service.chat(db, uuid.uuid4(), "research this") == "Done."
    assert create.await_args_list[1].kwargs.get("container") == "cont_abc123"
    assert create.await_args_list[2].kwargs.get("container") == "cont_abc123"


@pytest.mark.asyncio
async def test_a_stale_partial_is_not_returned_after_the_turn_moves_on(db, _loop, mocker):
    """Once past the pause, a later unrelated failure must not answer with text
    from the pause."""
    mocker.patch.object(
        claude_service._client.messages, "create",
        new=mocker.AsyncMock(side_effect=[
            _paused("half an answer"),
            _response("tool_use", [_block(type="tool_use", id="t1", name="nope", input={})]),
            _container_error(),
            _container_error(),   # the drop-web-tools retry
        ]),
    )
    mocker.patch.object(claude_service, "get_handler", return_value=None)

    with pytest.raises(anthropic.BadRequestError):
        await claude_service.chat(db, uuid.uuid4(), "research this")
