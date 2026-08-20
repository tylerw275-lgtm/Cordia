import re
import uuid
from typing import Sequence

from sqlalchemy import Integer, case, cast, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory

# Words that carry no retrieval signal in a text message. Without this, every
# query matches on "what"/"you"/"the" and ranking is meaningless.
_STOPWORDS = frozenset("""
about above after again against all also and any are been before being below between both
but can cant come could did does doing dont down during each few for from further had has
have having her here hers him his how into its itself just know let like make may me more
most much must myself need nor not now off once only other ought our ours out over own
please same she should some such than that the their theirs them then there these they
this those through too under until very want was way were what when where which while who
whom why will with would you your yours yourself hey hi thanks thank tell got get give
""".split())

_MAX_TERMS = 8


async def store_memory(
    db: AsyncSession,
    category: str,
    subject: str,
    content: str,
    tags: list[str] | None = None,
    family_member_id: str | None = None,
    source: str = "conversation",
) -> Memory:
    mem = Memory(
        category=category,
        subject=subject,
        content=content,
        tags=tags,
        family_member_id=uuid.UUID(family_member_id) if family_member_id else None,
        source=source,
    )
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return mem


def _like_escape(value: str) -> str:
    """Escape LIKE wildcards so query text matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tokenize(query: str) -> list[str]:
    """Reduce a message to the terms worth searching on.

    The whole point: a memory search used to be `LIKE '%<the entire text
    message>%'`, which matched only if a stored memory literally contained the
    sentence — so it never fired. Match on keywords instead.
    """
    terms: list[str] = []
    for word in re.findall(r"[a-z0-9']+", (query or "").lower()):
        if len(word) <= 2 or word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
        if len(terms) == _MAX_TERMS:
            break
    return terms


async def search_memories(
    db: AsyncSession,
    query: str,
    category: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    exclude_categories: Sequence[str] | None = None,
) -> Sequence[Memory]:
    """Keyword search over stored memories, ranked by how many terms hit."""
    stmt = select(Memory)
    if category:
        stmt = stmt.where(Memory.category == category)
    if exclude_categories:
        stmt = stmt.where(Memory.category.notin_(list(exclude_categories)))
    if tags:
        stmt = stmt.where(Memory.tags.overlap(tags))

    terms = tokenize(query)
    if terms:
        patterns = [f"%{_like_escape(t)}%" for t in terms]
        matches = [
            or_(Memory.subject.ilike(p, escape="\\"), Memory.content.ilike(p, escape="\\"))
            for p in patterns
        ]
        # Rank by number of distinct terms matched, so five results are the five
        # most relevant rather than five that happened to share one word.
        score = literal(0)
        for m in matches:
            score = score + case((m, 1), else_=0)
        score = cast(score, Integer)
        stmt = stmt.where(or_(*matches)).order_by(score.desc(), Memory.updated_at.desc())
    else:
        # Nothing searchable in the query (empty, or all stopwords) — fall back
        # to the most recent memories rather than returning nothing.
        stmt = stmt.order_by(Memory.updated_at.desc())

    result = await db.execute(stmt.limit(limit))
    return result.scalars().all()


async def get_memories_by_category(db: AsyncSession, category: str) -> Sequence[Memory]:
    result = await db.execute(
        select(Memory).where(Memory.category == category).order_by(Memory.updated_at.desc())
    )
    return result.scalars().all()


async def update_memory(db: AsyncSession, memory_id: uuid.UUID, content: str) -> Memory | None:
    result = await db.execute(select(Memory).where(Memory.id == memory_id))
    mem = result.scalar_one_or_none()
    if mem:
        mem.content = content
        await db.commit()
        await db.refresh(mem)
    return mem
