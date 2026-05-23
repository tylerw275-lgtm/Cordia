import pytest

from app.services import memory_service


@pytest.mark.asyncio
async def test_store_and_recall_memory(db):
    mem = await memory_service.store_memory(
        db,
        category="preference",
        subject="seat preference",
        content="Prefers aisle seats on all flights over 2 hours",
        tags=["travel", "flights"],
        source="test",
    )
    assert mem.id is not None
    assert mem.category == "preference"

    results = await memory_service.search_memories(db, query="seat preference")
    assert any(m.id == mem.id for m in results)


@pytest.mark.asyncio
async def test_memory_category_filter(db):
    await memory_service.store_memory(db, category="fact", subject="home city", content="Based in Nashville, TN", source="test")
    await memory_service.store_memory(db, category="preference", subject="coffee", content="Prefers black coffee", source="test")

    facts = await memory_service.get_memories_by_category(db, "fact")
    assert all(m.category == "fact" for m in facts)
