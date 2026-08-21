"""Projects: interview first, research second, deliver third.

The reliability idea here is that the obligation to interview arrives as **tool
data**, not as prompt text. A system-prompt rule saying "ask questions first" is
skimmable; a tool result that hands back five questions and says nothing has been
answered yet is not. For an ask no playbook anticipated, `start_project` hands
back a question-*design* brief instead and requires the derived questions to be
committed before any answer — same property, no pre-written list needed.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.project import Project
from app.prompts import playbooks

logger = logging.getLogger(__name__)


TOOL_SCHEMAS = [
    {
        "name": "start_project",
        "description": (
            "Open a project for any ask that deserves a real interview and real research "
            "rather than an immediate answer — outfitting a place, sourcing and pricing a "
            "service, planning an event, or researching a decision. Call this FIRST, before "
            "answering. It returns the questions to ask her (or, if the ask is unusual, a "
            "brief for designing them). Do not answer the request until she has had a chance "
            "to reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short name, e.g. 'Naples house setup'"},
                "request": {"type": "string", "description": "What she asked, in her words"},
                "kind": {
                    "type": "string",
                    "description": (
                        "Optional. One of: place_setup, service_sourcing, event_planning, "
                        "research_brief. Omit to let it be matched or derived."
                    ),
                },
            },
            "required": ["title", "request"],
        },
    },
    {
        "name": "save_project_questions",
        "description": (
            "Record the questions you designed for a project that had no stock interview. "
            "Required before you answer such a request — it makes the interview durable, so a "
            "reply days later still lines up with what was asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["project_id", "questions"],
        },
    },
    {
        "name": "save_project_answers",
        "description": (
            "Save what she answered. Partial answers are fine and expected — save whatever "
            "came back and ask only for what is still missing. Never re-ask something already "
            "answered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "answers": {
                    "type": "array",
                    "description": "One entry per question she addressed.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question, or enough of it to match"},
                            "answer": {"type": "string"},
                        },
                        "required": ["question", "answer"],
                    },
                },
            },
            "required": ["project_id", "answers"],
        },
    },
    {
        "name": "get_project",
        "description": (
            "Read a project back — the questions, what she has answered, findings and quotes "
            "so far. Use this when picking up work from an earlier conversation rather than "
            "relying on memory of the thread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "list_projects",
        "description": "List projects, most recent first. Use when she asks what's outstanding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional filter: intake, researching, delivered, closed",
                },
            },
        },
    },
    {
        "name": "save_project_findings",
        "description": (
            "Record what your research turned up, each with the page you read it from. Facts "
            "without a source do not belong here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fact": {"type": "string"},
                            "source_url": {"type": "string"},
                        },
                        "required": ["fact", "source_url"],
                    },
                },
            },
            "required": ["project_id", "findings"],
        },
    },
    {
        "name": "save_quote_options",
        "description": (
            "Record priced options for something she may buy or book. Every option needs the "
            "page the price came from — an uncited price is worse than no price, because she "
            "will act on it. You never pay; you hand her a link or a number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "vendor": {"type": "string"},
                            "price": {"type": "string", "description": "Total she would actually pay, e.g. '$780'"},
                            "whats_included": {"type": "string"},
                            "excludes": {"type": "string", "description": "Gratuity, tolls, fees, overtime"},
                            "cancellation": {"type": "string"},
                            "source_url": {"type": "string", "description": "The page this price came from — required"},
                            "booking_url": {"type": "string"},
                            "phone": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["vendor", "price", "source_url"],
                    },
                },
            },
            "required": ["project_id", "options"],
        },
    },
    {
        "name": "deliver_project",
        "description": (
            "Save the finished deliverable and mark the project delivered. Email the full "
            "version with send_report_email and text her a short summary — long output does "
            "not belong in a text message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "deliverable": {"type": "string", "description": "The full result, in markdown"},
            },
            "required": ["project_id", "deliverable"],
        },
    },
]


async def _visible(db: AsyncSession, actor):
    """Whose projects this principal may read — their own, plus Cordia's if she
    has shared them. None means unrestricted (single-user deployment)."""
    if actor is None:
        return None
    from app.services import principal_service
    return await principal_service.visible_scope(db, actor, "projects")


async def _load(db: AsyncSession, project_id: str, actor=None) -> Project | None:
    try:
        pid = uuid.UUID(str(project_id))
    except (ValueError, AttributeError, TypeError):
        return None
    project = (await db.execute(select(Project).where(Project.id == pid))).scalars().first()
    if project is None:
        return None
    scope = await _visible(db, actor)
    if scope is not None:
        allowed, unowned = scope
        # A project someone may not see is reported as not found, not as
        # forbidden: "you can't see that" confirms it exists, itself a leak.
        if project.owner_user_id is None:
            if not unowned:
                return None
        elif project.owner_user_id not in allowed:
            return None
    return project


def _missing(project: Project) -> list[str]:
    return [q["question"] for q in (project.brief or []) if not q.get("answer")]


async def start_project_handler(db: AsyncSession, **kw) -> dict:
    request = kw.get("request", "")
    kind = kw.get("kind") or playbooks.match(request)
    book = playbooks.get(kind)

    actor = kw.get("acting_user")
    project = Project(
        title=kw["title"][:255],
        kind=kind if book else "derived",
        request=request,
        status="intake",
        requested_by=getattr(actor, "name", None),
        owner_user_id=getattr(actor, "id", None),
    )

    if book:
        project.brief = [{"question": q, "answer": None} for q in book["intake_questions"]]
        db.add(project)
        await db.commit()
        await db.refresh(project)
        return {
            "project_id": str(project.id),
            "kind": project.kind,
            "expert_framing": book["expert_framing"],
            "intake_questions": book["intake_questions"],
            "research_checklist": book["research_checklist"],
            "output_format": book["output_template"],
            "domain_notes": book.get("domain_notes"),
            "answers_on_file": 0,
            "next_step": (
                "Send her these questions as ONE numbered text and stop there. Tell her she "
                "can answer partially or say 'use your best judgement'. Do NOT answer the "
                "request yet — the whole point is that the answer reflects her situation. "
                "When she replies, call save_project_answers, then research the checklist "
                "with web_search before writing anything."
            ),
        }

    # Nothing matched — the normal case for an ask nobody anticipated.
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return {
        "project_id": str(project.id),
        "kind": "derived",
        "question_design_brief": playbooks.QUESTION_DESIGN_BRIEF.format(
            dimensions=playbooks.dimensions_text()
        ),
        "next_step": (
            "Design the interview yourself using the brief above, call "
            "save_project_questions with it, then send her the questions as ONE numbered "
            "text. Do NOT answer the request before she has had a chance to reply."
        ),
    }


async def save_project_questions_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"saved": False, "reason": "unknown_project"}
    questions = [q for q in (kw.get("questions") or []) if str(q).strip()]
    if not questions:
        return {"saved": False, "reason": "no_questions",
                "message": "Design at least one question that would change your answer."}

    existing = {q["question"] for q in (project.brief or [])}
    brief = list(project.brief or [])
    brief += [{"question": q, "answer": None} for q in questions if q not in existing]
    project.brief = brief
    flag_modified(project, "brief")
    await db.commit()
    return {
        "saved": True, "project_id": str(project.id), "questions": questions,
        "next_step": "Send these to her as one numbered text. Do not answer the request yet.",
    }


def _norm(text: str) -> str:
    return "".join(c for c in (text or "").lower() if c.isalnum())


async def save_project_answers_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"saved": False, "reason": "unknown_project"}

    # Rebuild as fresh dicts rather than editing the loaded ones in place.
    # SQLAlchemy does not track mutations inside a JSONB value: editing the
    # existing dicts also edits the loaded copy it compares against, the row
    # looks unchanged, no UPDATE is emitted, and her answers vanish silently.
    brief = [dict(b) for b in (project.brief or [])]
    recorded = []
    for item in kw.get("answers") or []:
        question, answer = item.get("question", ""), item.get("answer", "")
        if not answer:
            continue
        key = _norm(question)
        # Match loosely: the model rarely echoes a question back verbatim, and a
        # near-miss must not silently create a duplicate she gets re-asked.
        hit = next(
            (b for b in brief if key and (key in _norm(b["question"]) or _norm(b["question"]) in key)),
            None,
        )
        if hit is None:
            brief.append({"question": question, "answer": answer})
        else:
            hit["answer"] = answer
        recorded.append(question)

    project.brief = brief
    flag_modified(project, "brief")
    still_missing = _missing(project)
    if not still_missing and project.status == "intake":
        project.status = "researching"
    await db.commit()

    return {
        "saved": True,
        "project_id": str(project.id),
        "recorded": recorded,
        "still_missing": still_missing,
        "next_step": (
            "All answered. Research now — use web_search for anything current, save what you "
            "find with save_project_findings, then deliver."
            if not still_missing else
            "Ask only for what is still missing. Never re-ask something she already answered. "
            "If she would rather not say, use your judgement and tell her what you assumed."
        ),
    }


async def get_project_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"found": False, "reason": "unknown_project"}
    book = playbooks.get(project.kind)
    return {
        "found": True,
        "project_id": str(project.id),
        "title": project.title,
        "kind": project.kind,
        "status": project.status,
        "request": project.request,
        "brief": project.brief or [],
        "still_missing": _missing(project),
        "findings": project.findings or [],
        "quotes": project.quotes or [],
        "deliverable": project.deliverable,
        "research_checklist": book["research_checklist"] if book else None,
        "output_format": book["output_template"] if book else None,
    }


async def list_projects_handler(db: AsyncSession, **kw) -> dict:
    query = select(Project).order_by(Project.updated_at.desc()).limit(25)
    if kw.get("status"):
        query = query.where(Project.status == kw["status"])
    scope = await _visible(db, kw.get("acting_user"))
    if scope is not None:
        from app.services import principal_service
        query = query.where(
            principal_service.scope_filter(Project.owner_user_id, *scope)
        )
    rows = (await db.execute(query)).scalars().all()
    return {
        "count": len(rows),
        "projects": [
            {"project_id": str(p.id), "title": p.title, "status": p.status,
             "kind": p.kind, "still_missing": _missing(p)}
            for p in rows
        ],
    }


async def save_project_findings_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"saved": False, "reason": "unknown_project"}

    findings = list(project.findings or [])
    kept, rejected = [], []
    for f in kw.get("findings") or []:
        if not f.get("source_url"):
            rejected.append(f.get("fact", "")[:80])
            continue
        findings.append({"fact": f.get("fact", ""), "source_url": f["source_url"]})
        kept.append(f.get("fact", "")[:80])

    project.findings = findings
    flag_modified(project, "findings")
    if project.status == "intake":
        project.status = "researching"
    await db.commit()
    return {
        "saved": True, "kept": len(kept), "rejected": rejected,
        "message": (
            f"{len(rejected)} finding(s) had no source and were not saved. Go back and find "
            "where each came from, or drop it — do not tell her something you cannot point at."
            if rejected else "All findings saved with their sources."
        ),
    }


async def save_quote_options_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"saved": False, "reason": "unknown_project"}

    quotes = list(project.quotes or [])
    kept, rejected = [], []
    for o in kw.get("options") or []:
        if not o.get("source_url"):
            rejected.append(o.get("vendor", "unknown"))
            continue
        quotes.append({k: o.get(k) for k in (
            "vendor", "price", "whats_included", "excludes", "cancellation",
            "source_url", "booking_url", "phone", "notes",
        ) if o.get(k)})
        kept.append(o.get("vendor", "unknown"))

    project.quotes = quotes
    flag_modified(project, "quotes")
    await db.commit()
    return {
        "saved": True,
        "kept": kept,
        "rejected": rejected,
        "message": (
            f"Not saved, no source: {', '.join(rejected)}. Every price must come from a page "
            "you actually read this turn — she will act on these numbers."
            if rejected else "Options saved."
        ),
        "reminder": (
            "You do not pay for anything. Give her the booking link or the phone number and "
            "say exactly what to ask for. Never request or accept a card number."
        ),
    }


async def deliver_project_handler(db: AsyncSession, **kw) -> dict:
    project = await _load(db, kw.get("project_id"), kw.get("acting_user"))
    if project is None:
        return {"delivered": False, "reason": "unknown_project"}

    missing = _missing(project)
    project.deliverable = kw.get("deliverable", "")
    project.status = "delivered"
    await db.commit()
    return {
        "delivered": True,
        "project_id": str(project.id),
        "title": project.title,
        "answered_questions": len((project.brief or [])) - len(missing),
        "unanswered_questions": missing,
        "next_step": (
            "Email the full version with send_report_email and text her a short summary — "
            "two or three lines with the headline and what to do next."
            + (f" Say plainly which assumptions you made about: {'; '.join(missing)}."
               if missing else "")
        ),
    }


HANDLERS = {
    "start_project": start_project_handler,
    "save_project_questions": save_project_questions_handler,
    "save_project_answers": save_project_answers_handler,
    "get_project": get_project_handler,
    "list_projects": list_projects_handler,
    "save_project_findings": save_project_findings_handler,
    "save_quote_options": save_quote_options_handler,
    "deliver_project": deliver_project_handler,
}
