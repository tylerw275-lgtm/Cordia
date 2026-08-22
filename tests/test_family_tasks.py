"""Who still has to do what before the trip.

Cordia is taking fourteen people to Africa in 2027. A travel agent is doing the
booking; the herding falls to her — everyone needs a current passport, everyone
has to confirm a fare before the hold expires. Cord could ask one relative one
question. It could not hold the list, so it could not answer the only question
she will actually ask: who is still outstanding.

The constraint that shapes all of this: Cord never chases the assignee. The
family did not sign up to be nagged by an assistant, least of all one speaking
for her. Outstanding work reaches Cordia and nobody else, and several tests here
exist only to keep it that way.
"""
from datetime import date, timedelta

import pytest

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.family import FamilyMember
from app.models.task import FamilyTask
from app.services import task_service
from app.tools import task_tools

TODAY = date.today()


async def _family(db, *names):
    members = []
    for name in names:
        member = FamilyMember(name=name, relationship="son", has_circle_access=True,
                              phone=f"+1615555{1000 + len(members)}")
        db.add(member)
        members.append(member)
    await db.commit()
    return members


# --- the question she will actually ask -------------------------------------

@pytest.mark.asyncio
async def test_one_ask_becomes_one_item_per_person(db):
    """A single row with a list of assignees cannot record that one son renewed
    his passport and another has not."""
    await _family(db, "Elliot Harrington", "Theodore Harrington", "Dominic Harrington")

    result = await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=60)).isoformat(),
    )

    assert result["created"] == 3
    listed = await task_tools.list_family_tasks_handler(db)
    assert sorted(t["who"] for t in listed["tasks"]) == [
        "Dominic Harrington", "Elliot Harrington", "Theodore Harrington"]


@pytest.mark.asyncio
async def test_she_can_ask_who_is_still_outstanding(db):
    elliot, theo = await _family(db, "Elliot Harrington", "Theodore Harrington")
    await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=60)).isoformat())

    listed = await task_tools.list_family_tasks_handler(db)
    elliots = next(t for t in listed["tasks"] if t["who"] == "Elliot Harrington")
    await task_tools.update_family_task_handler(db, task_id=elliots["task_id"], status="done")

    still_open = await task_tools.list_family_tasks_handler(db)
    assert [t["who"] for t in still_open["tasks"]] == ["Theodore Harrington"]


@pytest.mark.asyncio
async def test_blocked_still_counts_as_outstanding(db):
    """"I've applied, it's in the post" is not done, and dropping it off the
    list is how it gets forgotten."""
    await _family(db, "Theodore Harrington")
    await task_tools.track_family_tasks_handler(db, title="Renew passport", people=["everyone"])
    task_id = (await task_tools.list_family_tasks_handler(db))["tasks"][0]["task_id"]

    await task_tools.update_family_task_handler(
        db, task_id=task_id, status="blocked", notes="Applied, waiting on the passport office")

    still_open = (await task_tools.list_family_tasks_handler(db))["tasks"]
    assert len(still_open) == 1
    assert "passport office" in still_open[0]["notes"]


@pytest.mark.asyncio
async def test_a_name_nobody_recognises_is_reported_not_dropped(db):
    """A list that quietly covers eleven of fourteen people is worse than no
    list, because she will trust it."""
    await _family(db, "Elliot Harrington")

    result = await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["Elliot", "Marguerite"])

    assert result["created"] == 1
    assert result["unknown_people"] == ["Marguerite"]
    assert "Marguerite" in result["message"]


@pytest.mark.asyncio
async def test_a_task_can_be_cordias_own(db):
    result = await task_tools.track_family_tasks_handler(db, title="Confirm the Qatar hold")
    assert result["created"] == 1
    assert (await task_tools.list_family_tasks_handler(db))["tasks"][0]["who"] == "Cordia"


# --- deadlines --------------------------------------------------------------

@pytest.mark.parametrize("offset,expected", [
    (-3, "3 days overdue"), (-1, "1 day overdue"), (0, "due today"),
    (1, "due tomorrow"), (12, "due in 12 days"),
])
def test_a_deadline_reads_the_way_a_person_would_say_it(offset, expected):
    task = FamilyTask(title="Renew passport", due_on=TODAY + timedelta(days=offset))
    assert task_service.describe_due(task, TODAY) == expected


def test_an_undated_task_says_so_rather_than_guessing():
    assert task_service.describe_due(FamilyTask(title="x"), TODAY) == "no date set"


@pytest.mark.asyncio
async def test_only_things_near_their_deadline_are_surfaced(db):
    """A brief that lists everything outstanding is a brief nobody finishes."""
    await _family(db, "Elliot Harrington")
    await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=10)).isoformat())
    await task_tools.track_family_tasks_handler(
        db, title="Book the anniversary dinner", people=["everyone"],
        due_on=(TODAY + timedelta(days=365)).isoformat())

    coming = await task_service.coming_due(db, TODAY)
    assert [t.title for t in coming] == ["Renew passport"]


@pytest.mark.asyncio
async def test_an_undated_task_is_never_surfaced_automatically(db):
    """There is nothing to be late for."""
    await _family(db, "Elliot Harrington")
    await task_tools.track_family_tasks_handler(db, title="Ideas for the itinerary",
                                                people=["everyone"])
    assert await task_service.coming_due(db, TODAY) == []


@pytest.mark.asyncio
async def test_fourteen_people_are_one_line_not_fourteen(db):
    """One thing everyone has to do is fourteen rows and one sentence."""
    await _family(db, *[f"Relative{i} Harrington" for i in range(14)])
    await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=12)).isoformat())

    lines = await task_service.brief_lines(db, TODAY)
    assert len(lines) == 1
    assert lines[0].startswith("Renew passport: ")
    assert "due in 12 days" in lines[0]


# --- Cord does not chase anyone ---------------------------------------------

@pytest.mark.asyncio
async def test_recording_a_task_sends_nothing_to_anyone(db, mocker):
    send_sms = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    send_email = mocker.patch("app.services.email_service.send_email", new=mocker.AsyncMock())
    await _family(db, "Elliot Harrington", "Theodore Harrington")

    await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=5)).isoformat())

    assert not send_sms.called
    assert not send_email.called


@pytest.mark.asyncio
async def test_outstanding_work_reaches_cordia_and_nobody_else(db, mocker):
    """The whole point of tracking it is that she can chase people herself."""
    import contextlib
    from app.scheduler.jobs import morning_brief

    await _family(db, "Elliot Harrington", "Theodore Harrington")
    await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"],
        due_on=(TODAY + timedelta(days=9)).isoformat())

    @contextlib.asynccontextmanager
    async def _fake():
        yield db

    mocker.patch.object(morning_brief, "get_db_session", _fake)
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    mocker.patch("app.services.claude_service.record_assistant_message", new=mocker.AsyncMock())
    send = mocker.patch("app.services.sms_service.send_sms",
                        new=mocker.AsyncMock(return_value=True))

    await morning_brief.send_morning_brief()

    assert send.await_count == 1
    assert send.await_args.kwargs["to"] == "+16157080002"
    assert "Renew passport" in send.await_args.kwargs["body"]


def test_no_tool_here_can_send_anything():
    """A future edit that adds one should have to notice this test.

    Checked against the parsed module rather than its text, so a comment
    explaining that Cord does not send things is not mistaken for Cord sending
    things."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(task_tools))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert not called & {"send_sms", "send_email", "ask_family_member_handler"}, called


def test_the_family_role_cannot_see_the_task_list():
    """It is Cordia's view of who is behind. Handing it to a relative shows
    them what everyone else has not done."""
    from app.tools.registry import get_tool_schemas

    names = {t["name"] for t in get_tool_schemas("family")}
    assert not names & {"track_family_tasks", "list_family_tasks", "update_family_task"}


# --- the walls between principals -------------------------------------------

@pytest.mark.asyncio
async def test_toms_list_is_not_cordias(db):
    cordia = AuthorizedUser(name="Cordia Harrington", phone="+16157080002", is_owner=True)
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add_all([cordia, tom])
    await db.commit()
    await _family(db, "Elliot Harrington")

    await task_tools.track_family_tasks_handler(
        db, acting_user=cordia, title="Surprise for Tom", people=["everyone"])

    his = await task_tools.list_family_tasks_handler(db, acting_user=tom)
    assert his["count"] == 0


@pytest.mark.asyncio
async def test_a_task_someone_may_not_see_cannot_be_edited(db):
    """And is reported as not found rather than forbidden, since "you can't see
    that" confirms it exists."""
    cordia = AuthorizedUser(name="Cordia Harrington", phone="+16157080002", is_owner=True)
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add_all([cordia, tom])
    await db.commit()
    await _family(db, "Elliot Harrington")

    await task_tools.track_family_tasks_handler(
        db, acting_user=cordia, title="Surprise for Tom", people=["everyone"])
    hers = await task_tools.list_family_tasks_handler(db, acting_user=cordia)
    task_id = hers["tasks"][0]["task_id"]

    result = await task_tools.update_family_task_handler(
        db, acting_user=tom, task_id=task_id, status="done")
    assert result == {"updated": False, "reason": "unknown_task"}


# --- bad input --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_nonsense_id_is_refused_rather_than_raising(db):
    assert (await task_tools.update_family_task_handler(
        db, task_id="not-a-uuid", status="done"))["updated"] is False


@pytest.mark.asyncio
async def test_an_unparseable_date_does_not_lose_the_task(db):
    await _family(db, "Elliot Harrington")
    result = await task_tools.track_family_tasks_handler(
        db, title="Renew passport", people=["everyone"], due_on="next spring")
    assert result["created"] == 1
    assert (await task_tools.list_family_tasks_handler(db))["tasks"][0]["due"] == "no date set"
