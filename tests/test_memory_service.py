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


# ---------------------------------------------------------------------------
# Keyword recall. Previously search built LIKE '%<the whole message>%', so a
# real text message never matched anything and auto-recall silently never fired.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_matches_keyword_from_a_full_sentence(db):
    mem = await memory_service.store_memory(
        db, category="fact", subject="Pia gift ideas",
        content="Pia loves boba tea and getting her nails done", source="test",
    )
    results = await memory_service.search_memories(
        db, query="What do you know about my family and what Pia is into?"
    )
    assert any(m.id == mem.id for m in results)


@pytest.mark.asyncio
async def test_search_ranks_multi_term_matches_first(db):
    weak = await memory_service.store_memory(
        db, category="fact", subject="travel", content="She enjoys travel", source="test")
    strong = await memory_service.store_memory(
        db, category="fact", subject="Pia travel", content="Pia enjoys travel and boba", source="test")
    results = await memory_service.search_memories(db, query="what does Pia like about travel and boba")
    assert results[0].id == strong.id
    assert weak.id in [m.id for m in results]


def test_tokenizer_never_emits_like_metacharacters():
    # LIKE patterns are built from these tokens, so a wildcard must never
    # survive tokenization (and the patterns are escaped besides).
    for term in memory_service.tokenize("100%% sure about nash_ville \\ escapes"):
        assert "%" not in term and "_" not in term and "\\" not in term


@pytest.mark.asyncio
async def test_wildcard_only_query_does_not_error_or_wildcard_match(db):
    await memory_service.store_memory(
        db, category="fact", subject="home city", content="Based in Nashville", source="test")
    # No searchable terms — falls back to recent rather than erroring or
    # letting "%" act as a match-everything pattern.
    results = await memory_service.search_memories(db, query="%%% ___")
    assert len(results) <= 1


@pytest.mark.asyncio
async def test_stopword_only_query_falls_back_to_recent(db):
    mem = await memory_service.store_memory(
        db, category="fact", subject="home city", content="Based in Nashville", source="test")
    results = await memory_service.search_memories(db, query="what do you know?")
    assert any(m.id == mem.id for m in results)


@pytest.mark.asyncio
async def test_tags_filter_applies(db):
    tagged = await memory_service.store_memory(
        db, category="preference", subject="seat", content="Aisle seats", tags=["flights"], source="test")
    await memory_service.store_memory(
        db, category="preference", subject="coffee", content="Black coffee", tags=["food"], source="test")
    results = await memory_service.search_memories(db, query="seat coffee", tags=["flights"])
    assert [m.id for m in results] == [tagged.id]


@pytest.mark.asyncio
async def test_excluded_categories_are_omitted(db):
    await memory_service.store_memory(
        db, category="feature_request", subject="calendar sync",
        content="Wants calendar sync with Outlook", source="test")
    results = await memory_service.search_memories(
        db, query="calendar sync", exclude_categories=["feature_request"])
    assert results == []
