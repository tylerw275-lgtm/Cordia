"""Turn a JSON body into model keyword arguments, safely.

`Trip(**body)` and `Lease(**body)` were mass assignment: every key in the
request became a constructor argument. A typo produced
`TypeError: 'destinaton' is an invalid keyword argument`, which FastAPI turns
into a 500 — an error that reads like the server broke rather than like the
request was wrong. A caller could also set `id` or `created_at`.

Both endpoints sit behind admin auth, so this was never an open door. It was
still the wrong shape: unknown fields should be a 422 naming the field, and
columns the database owns should not be settable from outside.
"""
from typing import Any

from fastapi import HTTPException

# Set by the database or the application, never by a request body.
_SERVER_OWNED = {"id", "created_at", "updated_at"}


def model_kwargs(model, body: dict[str, Any]) -> dict[str, Any]:
    """Keep the keys that are real columns on `model`, reject the rest."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")

    columns = {c.key for c in model.__table__.columns} - _SERVER_OWNED
    unknown = sorted(set(body) - columns - _SERVER_OWNED)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown field(s) for {model.__name__}: {', '.join(unknown)}",
        )
    return {k: v for k, v in body.items() if k in columns}


def required(body: dict[str, Any], *names: str) -> None:
    missing = [n for n in names if not body.get(n)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required field(s): {', '.join(missing)}")
