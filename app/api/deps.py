import hmac
from typing import AsyncGenerator

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def require_admin(
    request: Request,
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
) -> None:
    """Gate the data APIs.

    Two ways in, and deliberately **no ``?secret=`` query param**: a secret in a
    URL is written to every access log it passes through, so simply using the
    dashboard leaked the admin secret into log retention. Accepts either the
    ``X-Admin-Secret`` header (for scripts — headers are not logged) or a valid
    dashboard session cookie, which is how the dashboard's own links now reach
    these routes.

    Fails *closed* when ``admin_api_secret`` is unset — these routes expose
    family PII and the full text of Cordia's conversations, so a deploy that
    forgets the env var must deny rather than serve them to the open internet.
    """
    # Imported here: app.api.dashboard imports settings and would otherwise
    # create a cycle through this module.
    from app.api.dashboard import session_is_valid

    if session_is_valid(request):
        return

    expected = settings.admin_api_secret
    supplied = x_admin_secret or ""
    # Compare as bytes: hmac.compare_digest raises TypeError on non-ASCII str,
    # which would turn a bad header into a 500 instead of a 401.
    if not expected or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Unauthorized")
