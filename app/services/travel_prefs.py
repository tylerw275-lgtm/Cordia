"""Cordia's travel preferences: storage on the Memory table + pure logic for
applying them to flight search results.

Preferences learned so far (asked once, then updated as she overrides):
- nonstop_preferred (assumed True — she always prefers non-stop)
- priority: 'fastest' | 'cheapest'
- avoid_cities: connection airports/cities she'd rather not route through
- preferred_airlines, cabin
- loyalty_programs: {program: member_number_on_file_bool_or_note}

Anticipation rule: when a stored preference hides a notably better option, we
don't silently drop it — we return it as a tradeoff so Cord can surface it in
one line and let her decide.
"""
import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory

PREFS_SUBJECT = "travel_preferences"

DEFAULT_PREFS = {
    "nonstop_preferred": True,
    "priority": None,          # 'fastest' | 'cheapest' | None (not yet asked)
    "avoid_cities": [],        # IATA codes or city names
    "preferred_airlines": [],  # IATA carrier codes
    "cabin": None,
    "loyalty_programs": {},    # e.g. {"Delta SkyMiles": "on file", "Marriott Bonvoy": "on file"}
}


async def load_prefs(db: AsyncSession) -> dict:
    result = await db.execute(
        select(Memory).where(Memory.category == "preference").where(Memory.subject == PREFS_SUBJECT)
    )
    mem = result.scalars().first()
    prefs = dict(DEFAULT_PREFS)
    if mem:
        try:
            prefs.update(json.loads(mem.content))
        except (json.JSONDecodeError, TypeError):
            pass
    return prefs


async def save_prefs(db: AsyncSession, updates: dict) -> dict:
    result = await db.execute(
        select(Memory).where(Memory.category == "preference").where(Memory.subject == PREFS_SUBJECT)
    )
    mem = result.scalars().first()
    prefs = dict(DEFAULT_PREFS)
    if mem:
        try:
            prefs.update(json.loads(mem.content))
        except (json.JSONDecodeError, TypeError):
            pass
    # Merge: lists replace, loyalty dict merges, scalars replace
    for key, value in updates.items():
        if key == "loyalty_programs" and isinstance(value, dict):
            prefs.setdefault("loyalty_programs", {}).update(value)
        elif value is not None:
            prefs[key] = value
    if mem:
        mem.content = json.dumps(prefs)
    else:
        db.add(Memory(category="preference", subject=PREFS_SUBJECT,
                      content=json.dumps(prefs), source="conversation"))
    await db.commit()
    return prefs


def parse_duration_minutes(iso: str | None) -> int | None:
    """'PT5H30M' -> 330. Returns None if unparseable."""
    if not iso:
        return None
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m or not any(m.groups()):
        return None
    days, hours, minutes = (int(g) if g else 0 for g in m.groups())
    return days * 1440 + hours * 60 + minutes


def _violates_avoid(offer: dict, avoid: list[str]) -> str | None:
    """Return the avoided city/code the offer routes through, if any."""
    vias = [v.upper() for v in offer.get("via") or []]
    for a in avoid:
        if a.upper() in vias:
            return a
    return None


def apply_preferences(offers: list[dict], prefs: dict) -> tuple[list[dict], list[dict]]:
    """Split offers into (matching, tradeoffs).

    matching: offers that satisfy non-stop + avoid-city preferences, sorted by
    her priority (fastest/cheapest; price as fallback).
    tradeoffs: excluded offers notably better on the priority metric than the
    best matching option (>=60 min faster or >=15% cheaper), annotated with
    why they were excluded — for Cord to surface as a one-line heads-up.
    """
    if not offers:
        return [], []

    avoid = prefs.get("avoid_cities") or []
    nonstop = bool(prefs.get("nonstop_preferred", True))
    priority = prefs.get("priority")

    matching, excluded = [], []
    for o in offers:
        reason = None
        avoided_via = _violates_avoid(o, avoid)
        if avoided_via:
            reason = f"connects through {avoided_via}, which she avoids"
        elif nonstop and (o.get("stops") or 0) > 0:
            reason = f"{o['stops']} stop(s) — she prefers non-stop"
        if reason:
            excluded.append({**o, "excluded_because": reason})
        else:
            matching.append(dict(o))

    # If preferences filtered out everything, fall back to all offers so she
    # still gets options (with the reasons attached).
    if not matching:
        return sorted(excluded, key=lambda o: o["price"]), []

    def sort_key(o: dict):
        if priority == "fastest":
            return (parse_duration_minutes(o.get("duration")) or 10**6, o["price"])
        return (o["price"], parse_duration_minutes(o.get("duration")) or 10**6)

    matching.sort(key=sort_key)
    best = matching[0]
    best_minutes = parse_duration_minutes(best.get("duration")) or 10**6

    tradeoffs = []
    for o in excluded:
        o_minutes = parse_duration_minutes(o.get("duration"))
        much_faster = o_minutes is not None and best_minutes - o_minutes >= 60
        much_cheaper = o["price"] <= best["price"] * 0.85
        if much_faster or much_cheaper:
            tradeoffs.append(o)
    tradeoffs.sort(key=lambda o: o["price"])
    return matching, tradeoffs[:2]
