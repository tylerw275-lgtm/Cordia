import hmac
from typing import AsyncGenerator

from fastapi import Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def require_admin(
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
    secret: str | None = Query(default=None),
) -> None:
    """Gate the data APIs behind a shared secret.

    Accepts the secret as either an ``X-Admin-Secret`` header or a ``?secret=``
    query param, mirroring the Signal House webhook. Fails *closed* when
    ``admin_api_secret`` is unset — these routes expose family PII and the full
    text of Cordia's conversations, so a deploy that forgets the env var must
    deny rather than serve them to the open internet.
    """
    expected = settings.admin_api_secret
    supplied = x_admin_secret or secret or ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
