"""Owner tools: the list of what everyone still has to do.

Cordia's question is never "show me the task table". It is "who still needs a
passport", asked six months before a trip, and answered by name.

None of these send anything. Cord tracks and reports; the chasing is hers. When
she wants a particular relative asked a particular thing, ask_family_member
sends it once, with her behind it.
"""
import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyMember
from app.models.task import FamilyTask
from app.services import family_service, task_service
from app.tools.actor import wants_actor

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "name": "track_family_tasks",
        "description": (
            "Record something one or more people each need to DO before a date — updated "
            "passports before a trip, confirming a fare before a hold expires, sending "
            "their ideas. Creates one item per person so you can later say who is still "
            "outstanding by name. Use whenever she says everyone needs to do something, "
            "or asks you to keep track of who has. This only records it: you never "
            "message the people it is assigned to."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The thing to be done, short. e.g. 'Renew passport'"},
                "people": {
                    "type": "array", "items": {"type": "string"},
                    "description": ("Family member names it applies to. Use ['everyone'] for the "
                                    "whole circle, or omit for Cordia's own task."),
                },
                "due_on": {"type": "string", "description": "YYYY-MM-DD, if there is a deadline"},
                "detail": {"type": "string", "description": "Anything useful about what is needed"},
                "project_id": {"type": "string", "description": "Link it to an open project, if there is one"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_family_tasks",
        "description": (
            "Who is still outstanding on what. Call this whenever she asks who still needs "
            "to do something, what is left before a trip, or what is coming due. Answer her "
            "by name and by how long is left, not by listing rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "blocked", "skipped", "all"],
                           "description": "Defaults to open (still outstanding)."},
                "person": {"type": "string", "description": "Only this family member's items"},
                "project_id": {"type": "string", "description": "Only items linked to this project"},
            },
        },
    },
    {
        "name": "update_family_task",
        "description": (
            "Mark one person's item done, blocked, or not applicable — when she tells you "
            "someone has sorted it, or a relative mentions it themselves. 'blocked' keeps it "
            "on the outstanding list with the reason in notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The item's id, from list_family_tasks"},
                "status": {"type": "string", "enum": ["open", "done", "blocked", "skipped"]},
                "notes": {"type": "string", "description": "What they said, if anything"},
            },
            "required": ["task_id", "status"],
        },
    },
]


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _assignees(db: AsyncSession, people) -> tuple[list, list[str]]:
    """Resolve names to family members. Returns (members, names_not_found).

    An unmatched name is reported rather than dropped: a task list that quietly
    covers eleven of fourteen people is worse than no task list, because she
    will trust it.
    """
    if not people:
        return [None], []
    wanted = [str(p).strip() for p in people if str(p).strip()]
    if len(wanted) == 1 and wanted[0].lower() in ("everyone", "all", "the family", "everybody"):
        members = (await db.execute(
            select(FamilyMember).order_by(FamilyMember.name)
        )).scalars().all()
        return list(members), []

    found, missing = [], []
    for name in wanted:
        if name.lower() in ("cordia", "me", "her", "herself"):
            found.append(None)
            continue
        member = await family_service.get_family_member_by_name(db, name)
        if member is None:
            missing.append(name)
        else:
            found.append(member)
    return found, missing


async def track_family_tasks_handler(db: AsyncSession, acting_user=None, **kw) -> dict:
    assignees, missing = await _assignees(db, kw.get("people"))
    if not assignees:
        return {"created": 0, "unknown_people": missing,
                "message": ("None of those names are on the family roster, so nothing was "
                            f"recorded. Ask Cordia who she means: {', '.join(missing)}.")}

    project_id = None
    if kw.get("project_id"):
        try:
            project_id = uuid.UUID(str(kw["project_id"]))
        except (ValueError, AttributeError):
            project_id = None

    tasks = await task_service.create(
        db, kw["title"], assignees,
        detail=kw.get("detail"), due_on=_parse_date(kw.get("due_on")),
        project_id=project_id, owner_user_id=getattr(acting_user, "id", None),
    )
    note = ""
    if missing:
        note = (f" Not on the roster, so not included: {', '.join(missing)} — "
                "tell her, and offer to add them.")
    return {
        "created": len(tasks), "title": kw["title"],
        "due_on": kw.get("due_on"), "unknown_people": missing,
        "message": (f"Tracking '{kw['title']}' for {len(tasks)} " +
                    ("person." if len(tasks) == 1 else "people.") + note +
                    " Do not message any of them about it — tell Cordia it is on the list."),
    }


async def list_family_tasks_handler(db: AsyncSession, acting_user=None, **kw) -> dict:
    assignee_id = None
    if kw.get("person"):
        member = await family_service.get_family_member_by_name(db, kw["person"])
        if member is None:
            return {"count": 0, "tasks": [],
                    "message": f"No family member on file named {kw['person']}."}
        assignee_id = member.id

    project_id = None
    if kw.get("project_id"):
        try:
            project_id = uuid.UUID(str(kw["project_id"]))
        except (ValueError, AttributeError):
            project_id = None

    status = kw.get("status") or "open"
    tasks = await task_service.visible(
        db, acting_user, status=None if status == "all" else status,
        project_id=project_id, assignee_id=assignee_id,
    )
    names = {m.id: m.name for m in (await db.execute(select(FamilyMember))).scalars()}
    return {
        "count": len(tasks),
        "tasks": [
            {"task_id": str(t.id), "title": t.title, "status": t.status,
             "who": names.get(t.assignee_member_id, "Cordia") if t.assignee_member_id else "Cordia",
             "due": task_service.describe_due(t),
             "due_on": t.due_on.isoformat() if t.due_on else None,
             "notes": t.notes}
            for t in tasks
        ],
        "message": ("Answer by name and by how long is left. Never offer to remind the people "
                    "themselves — Cord does not message anyone first."),
    }


async def update_family_task_handler(db: AsyncSession, acting_user=None, **kw) -> dict:
    try:
        task_id = uuid.UUID(str(kw["task_id"]))
    except (ValueError, AttributeError, KeyError):
        return {"updated": False, "reason": "bad_task_id"}

    task = (await db.execute(select(FamilyTask).where(FamilyTask.id == task_id))).scalars().first()
    if task is None:
        return {"updated": False, "reason": "unknown_task"}

    # Same walls as everything else: a task someone may not see is reported as
    # not found rather than forbidden, since "you can't see that" confirms it.
    if acting_user is not None:
        visible = await task_service.visible(db, acting_user, status=None)
        if task.id not in {t.id for t in visible}:
            return {"updated": False, "reason": "unknown_task"}

    try:
        await task_service.set_status(db, task, kw["status"], kw.get("notes"))
    except ValueError as e:
        return {"updated": False, "reason": str(e)}
    return {"updated": True, "task_id": str(task.id), "status": task.status,
            "title": task.title}


HANDLERS = wants_actor({
    "track_family_tasks": track_family_tasks_handler,
    "list_family_tasks": list_family_tasks_handler,
    "update_family_task": update_family_task_handler,
})
