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
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(engine):
    # Point the app's get_db at the function-scoped test engine so requests
    # don't touch the module-level engine bound to a different event loop.
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    # Webhooks acknowledge immediately and finish the work in a background
    # task, which opens its OWN session from the module-level engine — that
    # points at the production database name and is not overridable through
    # dependency_overrides. Without this, background handlers silently died on
    # a connection error and the webhook tests asserted against nothing.
    import contextlib

    import app.database as app_database

    @contextlib.asynccontextmanager
    async def _override_session():
        async with Session() as session:
            yield session

    real_session = app_database.get_db_session
    app_database.get_db_session = _override_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app_database.get_db_session = real_session
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
