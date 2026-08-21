"""What happens when a job is bigger than one pass.

The New York evening was five tool calls. A family trip to Africa is flights,
lodging, visas, vaccinations, a guide and transfers between them, and each one
is sequential: you cannot price the lodging until the guide's dates are fixed.
That is fifteen to twenty rounds, and the loop stopped at ten.

It did not stop gracefully. Ten API requests and fifteen real tool calls were
billed, every partial result was discarded, and she was told to rephrase — and
because the history window was rows rather than characters, the retry started
with less context than the first attempt rather than more.
"""
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.usage import UsageEvent
from app.services import claude_service
from tests.test_claude_history import assert_valid_transcript

OWNER = "+16157080002"


def _blk(**kw):
    b = SimpleNamespace(**kw)
    b.model_dump = lambda: dict(kw)
    return b


def _calls(tool, tool_input, *, tool_id, text=""):
    content = ([_blk(type="text", text=text)] if text else []) + [
        _blk(type="tool_use", id=tool_id, name=tool, input=tool_input)]
    return SimpleNamespace(stop_reason="tool_use", usage=None, container=None, content=content)


def _says(text):
    return SimpleNamespace(stop_reason="end_turn", usage=None, container=None,
                           content=[_blk(type="text", text=text)])


@pytest_asyncio.fixture
async def conversation(db):
    db.add(AuthorizedUser(name="Cordia Harrington", phone=OWNER, is_owner=True))
    await db.commit()
    return await claude_service.get_or_create_conversation(db, OWNER)


def _endless(mocker, *, finding: str, rounds: int = 40):
    """A model that keeps calling tools and reporting real findings."""
    n = {"i": 0}

    async def create(**kwargs):
        n["i"] += 1
        if n["i"] > rounds:
            return _says("done")
        return _calls("list_projects", {}, tool_id=f"tu_{n['i']}",
                      text=f"{finding} (leg {n['i']})")

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    return n


# --- the budget -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_tool_budget_comes_from_config(db, conversation, mocker):
    mocker.patch.object(settings, "max_tool_iterations", 4)
    calls = _endless(mocker, finding="X" * 200)

    await claude_service.chat(db, conversation.id, "hello")

    assert calls["i"] <= 6, "the loop ran past its configured budget"


@pytest.mark.asyncio
async def test_deep_work_gets_the_bigger_budget(db, conversation, mocker):
    """The whole point: the asks that need twenty rounds are exactly the ones
    the shallow budget cut off."""
    mocker.patch.object(settings, "max_tool_iterations", 3)
    mocker.patch.object(settings, "max_tool_iterations_deep", 20)
    calls = _endless(mocker, finding="Y" * 200)

    await claude_service.chat(db, conversation.id, "plan our family trip to Africa")

    assert calls["i"] > 3, "a deep-work ask was held to the shallow budget"


@pytest.mark.asyncio
async def test_a_twenty_call_job_returns_the_work_not_an_apology(db, conversation, mocker):
    """The plan's acceptance test. Twenty sequential tool calls, budget spent,
    and what comes back is the research — not "try rephrasing your request"."""
    mocker.patch.object(settings, "max_tool_iterations", 6)
    _endless(mocker, finding="Nairobi flights from BNA run about $1,480 return in "
                             "September, cheapest via Doha with one stop")

    reply = await claude_service.chat(db, conversation.id, "price this trip")

    assert "rephrase" not in reply.lower()
    assert "Nairobi flights" in reply, "every partial result was thrown away"
    assert "keep going" in reply, "she is not told how to continue"


@pytest.mark.asyncio
async def test_a_preamble_is_not_mistaken_for_progress(db, conversation, mocker):
    """"One moment" repeated six times is not a partial answer."""
    mocker.patch.object(settings, "max_tool_iterations", 3)
    _endless(mocker, finding="One moment.")

    reply = await claude_service.chat(db, conversation.id, "price this trip")
    assert "One moment." not in reply


@pytest.mark.asyncio
async def test_pause_resumes_do_not_eat_the_tool_budget(db, conversation, mocker):
    """Research is what a multi-vendor trip triggers, so three resumes used to
    leave seven rounds to do the actual work."""
    mocker.patch.object(settings, "max_tool_iterations", 4)
    seq = [
        SimpleNamespace(stop_reason="pause_turn", usage=None, container=None,
                        content=[_blk(type="text", text="searching")]),
        SimpleNamespace(stop_reason="pause_turn", usage=None, container=None,
                        content=[_blk(type="text", text="still searching")]),
        *[_calls("list_projects", {}, tool_id=f"tu_{i}") for i in range(4)],
        _says("All four legs priced."),
    ]
    queue = list(seq)

    async def create(**kwargs):
        return queue.pop(0)

    mocker.patch.object(claude_service._client.messages, "create", new=create)

    reply = await claude_service.chat(db, conversation.id, "price this trip")
    assert reply == "All four legs priced.", "two pauses cost two tool rounds"


@pytest.mark.asyncio
async def test_the_loop_always_terminates(db, conversation, mocker):
    """A model that alternates pause and tool_use forever must still stop."""
    mocker.patch.object(settings, "max_tool_iterations", 3)
    n = {"i": 0}

    async def create(**kwargs):
        n["i"] += 1
        if n["i"] % 2:
            return SimpleNamespace(stop_reason="pause_turn", usage=None, container=None,
                                   content=[_blk(type="text", text="...")])
        return _calls("list_projects", {}, tool_id=f"tu_{n['i']}")

    mocker.patch.object(claude_service._client.messages, "create", new=create)

    assert await claude_service.chat(db, conversation.id, "go")
    assert n["i"] < 30, f"the loop ran {n['i']} requests"


# --- failures the ledger can see -------------------------------------------

@pytest.mark.asyncio
async def test_a_broken_tool_shows_up_in_the_ledger(db, conversation, mocker):
    """A handler that cannot run is reported to the model as an ordinary result
    and to nobody else. That is precisely how memory stayed dead for six
    deploys with every dashboard green."""
    real = claude_service.get_handler

    def broken(name, role):
        if name == "list_projects":
            raise_it = mocker.AsyncMock(side_effect=RuntimeError("column does not exist"))
            return raise_it
        return real(name, role)

    mocker.patch.object(claude_service, "get_handler", new=broken)
    queue = [_calls("list_projects", {}, tool_id="tu_1"), _says("Could not check.")]
    mocker.patch.object(claude_service._client.messages, "create",
                        new=mocker.AsyncMock(side_effect=queue))

    await claude_service.chat(db, conversation.id, "what am I working on")

    errors = (await db.execute(
        select(UsageEvent).where(UsageEvent.event_type == "error")
    )).scalars().all()
    assert errors, "a tool blew up and nothing outside the logs knows"
    assert errors[0].details["where"] == "tool:list_projects"


# --- history that survives a long job ---------------------------------------

@pytest.mark.asyncio
async def test_two_deep_turns_no_longer_evict_the_conversation(db, conversation):
    """The measured failure: a single deep turn writes about 21 rows, so under a
    40-row window two of them erased everything before them."""
    await claude_service._persist_message(db, conversation.id, "user", "our Africa trip")
    await claude_service._persist_message(db, conversation.id, "assistant",
                                          [{"type": "text", "text": "Noted."}])
    for turn in range(2):
        for i in range(21):
            await claude_service._persist_message(
                db, conversation.id, "user", f"turn {turn} filler {i}")
            await claude_service._persist_message(
                db, conversation.id, "assistant",
                [{"type": "text", "text": f"ack {turn}.{i}"}])

    history = await claude_service._load_history(db, conversation.id)

    assert_valid_transcript(history)
    assert any("our Africa trip" in json.dumps(m) for m in history), \
        "two deep turns erased the start of the trip"


@pytest.mark.asyncio
async def test_one_enormous_tool_result_does_not_evict_everything_else(db, conversation):
    """A 30k-character search result replayed verbatim on every later turn was
    what actually pushed the conversation out of the window. The model already
    read it once and said what it concluded."""
    await claude_service._persist_message(db, conversation.id, "user", "find me flights")
    await claude_service._persist_message(db, conversation.id, "assistant", [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "tu_1", "name": "search_flights", "input": {}},
    ])
    await claude_service._persist_message(db, conversation.id, "tool", [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "Z" * 30_000},
    ])
    await claude_service._persist_message(db, conversation.id, "assistant",
                                          [{"type": "text", "text": "Cheapest is $1,480."}])

    history = await claude_service._load_history(db, conversation.id)

    assert_valid_transcript(history)
    assert "find me flights" in json.dumps(history), "the huge result evicted the question"
    replayed = json.dumps(history)
    assert "trimmed" in replayed
    assert len(replayed) < 12_000, "the oversized result was replayed in full"


@pytest.mark.asyncio
async def test_the_window_is_still_bounded(db, conversation):
    """Wider is not unbounded — this is the cost ceiling."""
    for i in range(400):
        await claude_service._persist_message(db, conversation.id, "user", f"msg {i} " + "q" * 400)
        await claude_service._persist_message(
            db, conversation.id, "assistant", [{"type": "text", "text": "ok " + "r" * 400}])

    history = await claude_service._load_history(db, conversation.id)
    assert len(json.dumps(history)) <= settings.history_max_chars * 1.5


@pytest.mark.asyncio
async def test_a_trimmed_window_never_starts_mid_exchange(db, conversation):
    """A leading tool_result is a 400, and a tight budget is exactly when the
    cut lands in the middle of one."""
    for i in range(30):
        await claude_service._persist_message(db, conversation.id, "user", f"q{i}")
        await claude_service._persist_message(db, conversation.id, "assistant", [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": f"tu_{i}", "name": "list_projects", "input": {}},
        ])
        await claude_service._persist_message(db, conversation.id, "tool", [
            {"type": "tool_result", "tool_use_id": f"tu_{i}", "content": "{}" + "x" * 500},
        ])
        await claude_service._persist_message(db, conversation.id, "assistant",
                                              [{"type": "text", "text": f"a{i}"}])

    for budget in (500, 2_000, 8_000, 48_000):
        history = await claude_service._load_history(db, conversation.id, max_chars=budget)
        assert_valid_transcript(history)


# --- work that outlives the window ------------------------------------------

@pytest.mark.asyncio
async def test_an_open_project_is_named_in_every_request(db, conversation, mocker):
    """A three-week trip outlives any window. What must survive is that the job
    exists and what it is still waiting on."""
    from app.models.project import Project

    db.add(Project(title="Africa family trip", kind="event_planning", status="researching",
                   brief=[{"question": "How many travelling?", "answer": "nine"},
                          {"question": "Which weeks in July?", "answer": None}]))
    await db.commit()

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _says("Yes.")

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    await claude_service.chat(db, conversation.id, "where were we")

    system = " ".join(b["text"] for b in sent[0]["system"])
    assert "Africa family trip" in system
    assert "Which weeks in July?" in system, "the open question was not carried forward"
    assert "How many travelling?" not in system, "an answered question was re-raised"


@pytest.mark.asyncio
async def test_a_delivered_project_stops_being_announced(db, conversation, mocker):
    from app.models.project import Project

    db.add(Project(title="NYC evening", kind="service_sourcing", status="delivered", brief=[]))
    await db.commit()

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _says("Yes.")

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    await claude_service.chat(db, conversation.id, "hello")

    assert "NYC evening" not in " ".join(b["text"] for b in sent[0]["system"])


@pytest.mark.asyncio
async def test_open_work_respects_the_walls_between_principals(db, conversation, mocker):
    """Tom must not learn what Cordia has open from a prompt block, of all
    places — it is the one path that never goes through a tool call."""
    from app.models.project import Project

    cordia = (await db.execute(select(AuthorizedUser))).scalars().first()
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add(tom)
    await db.commit()
    db.add(Project(title="Cordia's surprise for Tom", kind="event_planning",
                   status="researching", brief=[], owner_user_id=cordia.id))
    await db.commit()

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _says("Hello Tom.")

    mocker.patch.object(claude_service._client.messages, "create", new=create)
    toms_thread = await claude_service.get_or_create_conversation(db, "+16157080001")
    await claude_service.chat(db, toms_thread.id, "what have we got going on", sender_user=tom)

    assert "surprise" not in " ".join(b["text"] for b in sent[0]["system"])
