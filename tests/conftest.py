from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.database import Base
from app.main import app

TEST_DB_URL = "postgresql+asyncpg://cordia:password@localhost:5432/cordia_test"


@pytest_asyncio.fixture
async def engine():
    # Function-scoped so the engine lives in the same event loop as each test.
    # A session-scoped async engine collides with function-scoped event loops in
    # this pytest-asyncio version ("another operation is in progress" at teardown).
    # Drop + recreate each test for a clean, isolated schema (commits persist
    # otherwise, since handlers commit internally).
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # sms_consent has no ORM model (raw-SQL table from migration 003)
        await conn.exec_driver_sql("DROP TABLE IF EXISTS sms_consent")
        await conn.exec_driver_sql(
            "CREATE TABLE sms_consent ("
            "phone VARCHAR(20) PRIMARY KEY, "
            "consented_at TIMESTAMPTZ NOT NULL, "
            "method VARCHAR(50) NOT NULL, "
            "opted_out_at TIMESTAMPTZ, "
            # Migration 011: consent alone does not grant access.
            "approval_status VARCHAR(10) NOT NULL DEFAULT 'pending', "
            "reviewed_at TIMESTAMPTZ)"
        )
        # Migration 015. Also no ORM model; it used to be created lazily by the
        # form handler, which is exactly why every database nobody had signed
        # the form on was missing a table list_consent_requests joins.
        await conn.exec_driver_sql("DROP TABLE IF EXISTS consent_submissions")
        await conn.exec_driver_sql(
            "CREATE TABLE consent_submissions ("
            "id SERIAL PRIMARY KEY, full_name TEXT NOT NULL, "
            "phone VARCHAR(20) NOT NULL, submitted_at TIMESTAMPTZ NOT NULL)"
        )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def _session_override(engine):
    """Point app.database.get_db_session at the test engine.

    Code that opens its OWN session — background webhook handlers, the usage
    ledger — goes through the module-level engine, which names the production
    database and cannot be reached by dependency_overrides. Several of those
    call sites deliberately swallow their errors, so without this they fail
    silently in tests and the assertions pass against nothing.
    """
    import contextlib

    import app.database as app_database

    Session = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _override():
        async with Session() as session:
            yield session

    real = app_database.get_db_session
    app_database.get_db_session = _override
    try:
        yield Session
    finally:
        app_database.get_db_session = real


@pytest_asyncio.fixture
async def db(_session_override):
    async with _session_override() as session:
        yield session
        await session.rollback()


@pytest.fixture(autouse=True)
def _stream_follows_create(monkeypatch):
    """Make `messages.stream` delegate to whatever `messages.create` is.

    Deep-work turns ask for 32,000 output tokens, and above ~16,000 the SDK
    wants streaming or a slow generation hits the HTTP timeout — the same lost
    turn this budget exists to prevent, arriving by another route. So the deep
    path streams.

    In production the two differ. In a test they should not: `get_final_message()`
    returns the same Message object `create()` would, which is the whole reason
    the loop can be indifferent to which ran. Rather than have twenty test files
    each patch both, this makes a patched `create` cover both — so every existing
    mock keeps testing the real path, whichever one the token budget selects.
    """
    import contextlib

    from app.services import claude_service

    @contextlib.asynccontextmanager
    async def _stream(**kwargs):
        class _Streamed:
            async def get_final_message(self):
                return await claude_service._client.messages.create(**kwargs)

        yield _Streamed()

    monkeypatch.setattr(claude_service._client.messages, "stream", _stream,
                        raising=False)


@pytest.fixture(autouse=True)
def _reset_inbound_state():
    """Clear the module-level caches the SMS webhook keeps between requests.

    Seen-message ids, per-conversation locks and the holding-note history all
    live for the process's lifetime by design. Across a test session they leak:
    two files using the same MessageSid means the second one is silently dropped
    as a provider retry, and the failure looks like a logic bug in whichever
    test happens to run second.
    """
    from app.api import sms

    for cache in (sms._RECENT_MESSAGE_IDS, sms._INBOX, sms._INBOX_LOCKS, sms._RECENT_NOTES):
        cache.clear()
    yield


@pytest_asyncio.fixture
async def client(_session_override):
    # Point the app's get_db at the function-scoped test engine so requests
    # don't touch the module-level engine bound to a different event loop.
    # _session_override covers the background-task path at the same time.
    Session = _session_override

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def seed_doc_raw() -> str:
    """The raw JSON of an anonymized roster, structurally isomorphic to the
    real one: a single-word name, a daughter-in-law with no `parent`, a shared
    surname, aliases and contact details, and members with and without birthdays."""
    return (FIXTURE_DIR / "family_seed_sample.json").read_text(encoding="utf-8")


@pytest.fixture
def seed_doc(seed_doc_raw):
    from app.data.family_seed_loader import parse_seed_document

    return parse_seed_document(seed_doc_raw)
