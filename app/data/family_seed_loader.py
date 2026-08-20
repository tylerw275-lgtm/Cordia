"""Parse the family roster from configuration.

The roster used to be a Python literal in this package, which meant real names,
children's dates of birth, mobile numbers and home addresses sat in a public
git repository. It now arrives as JSON in FAMILY_SEED_JSON (or a local file for
development) and never touches source control.
"""
import base64
import binascii
import json
import logging
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.config import settings

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SeedInvalid(ValueError):
    """The seed document could not be parsed or failed validation."""


def _strict_date(value: Any) -> Any:
    """Accept only YYYY-MM-DD.

    date.fromisoformat on 3.11+ also accepts "20180313" and full datetimes, which
    would parse silently into something other than what was intended.
    """
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.match(value):
        return value
    raise ValueError("expected a date as YYYY-MM-DD")


class SeedMember(BaseModel):
    # forbid: without an explicit allow-list, a seed entry could set
    # has_circle_access on a NEW row — _BACKFILL_FIELDS only guards existing
    # ones — silently granting family-circle SMS access.
    model_config = ConfigDict(extra="forbid")

    name: str
    relationship: str
    nickname: str | None = None
    gender: str | None = None
    aliases: list[str] | None = None
    birthday: date | None = None
    anniversary: date | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    school_name: str | None = None
    grade_level: str | None = None
    interests: list[str] | None = None
    personality_notes: str | None = None
    parent: str | None = None

    _dates = field_validator("birthday", "anniversary", mode="before")(_strict_date)

    @field_validator("name", "relationship")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be empty")
        return v.strip()

    def to_kwargs(self) -> dict:
        """Columns to write. exclude_none so a null in the document can never
        overwrite an existing value with NULL."""
        return self.model_dump(exclude_none=True, exclude={"parent"})


class SeedActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    activity_date: date
    category: str
    participant_names: list[str]
    notes: str | None = None

    _dates = field_validator("activity_date", mode="before")(_strict_date)


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    members: list[SeedMember]
    activities: list[SeedActivity] = []

    @model_validator(mode="after")
    def _check_references(self) -> "SeedDocument":
        seen: set[str] = set()
        for m in self.members:
            key = m.name.lower()
            if key in seen:
                # Two rows with the same name make the exact-name lookup resolve
                # one and the other backfill onto it.
                raise ValueError(f"duplicate member name: {m.name}")
            seen.add(key)

        for m in self.members:
            if m.parent and m.parent.lower() not in seen:
                # A typo'd parent silently leaves parent_id null forever.
                raise ValueError(f"{m.name}: parent '{m.parent}' is not in members")

        for a in self.activities:
            unknown = [n for n in a.participant_names if n.lower() not in seen]
            if unknown:
                # Not an error: an activity can legitimately include a non-member.
                logger.warning(f"Activity '{a.title}' references non-members: {unknown}")
        return self


def parse_seed_document(raw: str) -> SeedDocument:
    """Parse and validate a seed document. Raises SeedInvalid."""
    text = (raw or "").strip()
    if not text:
        raise SeedInvalid("seed document is empty")

    # Tolerate a base64 paste, in case the JSON gets mangled in transit.
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise SeedInvalid("seed document is neither JSON nor base64-encoded JSON")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise SeedInvalid(f"seed document is not valid JSON: {e.msg} at line {e.lineno}") from e

    try:
        return SeedDocument.model_validate(payload)
    except Exception as e:
        # Path only, never the value — logs are less protected than the database.
        paths = "; ".join(
            ".".join(str(p) for p in err["loc"]) + ": " + err["msg"]
            for err in getattr(e, "errors", lambda: [])()
        ) or str(e)
        raise SeedInvalid(f"seed document failed validation: {paths}") from e


def load_seed_document() -> SeedDocument | None:
    """The configured roster, or None if none is configured.

    Returning None is a normal state, not an error: an already-seeded database
    needs no seed document at all.
    """
    if settings.family_seed_json.strip():
        return parse_seed_document(settings.family_seed_json)
    if settings.family_seed_path:
        try:
            with open(settings.family_seed_path, encoding="utf-8") as fh:
                return parse_seed_document(fh.read())
        except OSError as e:
            raise SeedInvalid(f"could not read {settings.family_seed_path}: {e}") from e
    return None
