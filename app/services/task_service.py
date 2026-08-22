"""Tasks across a group: who still has to do what, and by when.

Cordia is taking fourteen people to Africa in 2027. A travel agent is doing the
booking; what falls to her is the herding — everyone needs a current passport,
everyone has to say yes to a fare before the hold expires. Cord could already
ask one relative one question. It could not hold the list, so it could not
answer the only question she will actually ask: who is still outstanding.

What this deliberately does not do is chase anybody. The family did not sign up
to be nagged by an assistant, least of all one speaking for her, and Cord does
not message people who did not just message it. Outstanding work surfaces in the
morning brief, which goes to Cordia alone. When she wants a particular relative
asked a particular thing, she says so and ask_family_member sends it — with her
behind it, once, on purpose.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models.family import FamilyMember
from app.models.task import OPEN_STATUSES, STATUSES, FamilyTask

logger = logging.getLogger(__name__)


async def create(
    db: AsyncSession,
    title: str,
    assignees: list[FamilyMember | None],
    *,
    detail: str | None = None,
    due_on: date | None = None,
    project_id=None,
    owner_user_id=None,
) -> list[FamilyTask]:
    """One task row per assignee. `None` in the list means the principal herself."""
    tasks = []
    for member in (assignees or [None]):
        task = FamilyTask(
            title=title[:200], detail=detail, due_on=due_on,
            assignee_member_id=getattr(member, "id", None),
            owner_user_id=owner_user_id, project_id=project_id,
        )
        db.add(task)
        tasks.append(task)
    await db.commit()
    for task in tasks:
        await db.refresh(task)
    return tasks


async def visible(db: AsyncSession, actor, *, status: str | None = "open",
                  project_id=None, assignee_id=None) -> list[FamilyTask]:
    """Tasks this principal may see, soonest deadline first.

    Undated tasks sort last rather than first: a list headed by things with no
    deadline buries the one that is actually due.
    """
    stmt = select(FamilyTask)
    if actor is not None:
        from app.services import principal_service
        allowed, unowned = await principal_service.visible_scope(db, actor, "projects")
        stmt = stmt.where(principal_service.scope_filter(FamilyTask.owner_user_id, allowed, unowned))
    if status == "open":
        stmt = stmt.where(FamilyTask.status.in_(OPEN_STATUSES))
    elif status:
        stmt = stmt.where(FamilyTask.status == status)
    if project_id:
        stmt = stmt.where(FamilyTask.project_id == project_id)
    if assignee_id:
        stmt = stmt.where(FamilyTask.assignee_member_id == assignee_id)
    stmt = stmt.order_by(FamilyTask.due_on.is_(None), FamilyTask.due_on, FamilyTask.title)
    return list((await db.execute(stmt)).scalars())


async def set_status(db: AsyncSession, task: FamilyTask, status: str,
                     notes: str | None = None) -> FamilyTask:
    if status not in STATUSES:
        raise ValueError(f"unknown task status: {status}")
    task.status = status
    if notes:
        task.notes = notes
    task.completed_at = datetime.now(timezone.utc) if status == "done" else None
    await db.commit()
    await db.refresh(task)
    return task


async def coming_due(db: AsyncSession, today: date | None = None) -> list[FamilyTask]:
    """Open, dated tasks close enough to their deadline to be worth a mention.

    A task with no date is never surfaced this way: there is nothing to be late
    for, and putting it in a daily brief is how a brief stops being read.
    """
    today = today or date.today()
    stmt = (
        select(FamilyTask)
        .where(FamilyTask.status.in_(OPEN_STATUSES))
        .where(FamilyTask.due_on.isnot(None))
        .where(FamilyTask.due_on <= today + timedelta(days=settings.task_lead_days))
        .order_by(FamilyTask.due_on)
    )
    return list((await db.execute(stmt)).scalars())


def describe_due(task: FamilyTask, today: date | None = None) -> str:
    if task.due_on is None:
        return "no date set"
    days = (task.due_on - (today or date.today())).days
    if days < 0:
        return f"{abs(days)} day{'s' if abs(days) != 1 else ''} overdue"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days} days"


async def brief_lines(db: AsyncSession, today: date | None = None) -> list[str]:
    """One line per outstanding task, grouped by title.

    "Renew passport: Elliot, Theo (due in 12 days)" rather than one line each —
    fourteen relatives means fourteen rows for a single thing, and a brief that
    lists them separately is a brief nobody finishes reading.
    """
    tasks = await coming_due(db, today)
    if not tasks:
        return []

    names = {
        m.id: (m.nickname or m.name.split()[0])
        for m in (await db.execute(select(FamilyMember))).scalars()
    }
    grouped: dict[tuple[str, object], list[str]] = {}
    for task in tasks:
        key = (task.title, task.due_on)
        who = names.get(task.assignee_member_id, "you") if task.assignee_member_id else "you"
        grouped.setdefault(key, []).append(who)

    lines = []
    for (title, _), people in grouped.items():
        example = next(t for t in tasks if t.title == title)
        lines.append(f"{title}: {', '.join(people)} ({describe_due(example, today)})")
    return lines


async def mark_surfaced(db: AsyncSession, tasks: list[FamilyTask]) -> None:
    now = datetime.now(timezone.utc)
    for task in tasks:
        task.last_surfaced_at = now
    await db.commit()
