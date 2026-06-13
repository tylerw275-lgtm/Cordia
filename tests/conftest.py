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
            "opted_out_at TIMESTAMPTZ)"
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

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
