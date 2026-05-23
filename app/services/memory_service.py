import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory


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


async def search_memories(
    db: AsyncSession,
    query: str,
    category: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> Sequence[Memory]:
    stmt = select(Memory)
    if category:
        stmt = stmt.where(Memory.category == category)
    # Simple keyword match on subject + content (no vector search for MVP)
    if query:
        q = f"%{query.lower()}%"
        from sqlalchemy import or_, func
        stmt = stmt.where(
            or_(
                func.lower(Memory.subject).like(q),
                func.lower(Memory.content).like(q),
            )
        )
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    result = await db.execute(stmt)
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
