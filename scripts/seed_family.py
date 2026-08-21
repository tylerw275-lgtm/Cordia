"""Load Cordia's family data into the database by hand.

The app also does this on every boot (see app.main.lifespan), so this script is
only needed for a one-off load against a database the app isn't running against:

    python scripts/seed_family.py
"""
import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from app.data.family_seed_loader import SeedInvalid, load_seed_document, parse_seed_document
from app.database import get_db_session
from app.services.family_seed import seed_family


def _read(path: str | None):
    if path:
        with open(path, encoding="utf-8") as fh:
            return parse_seed_document(fh.read())
    return load_seed_document()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="read the roster from this JSON file")
    ap.add_argument("--check", action="store_true",
                    help="validate the document and print a summary without touching the database")
    args = ap.parse_args()

    try:
        seed = _read(args.file)
    except SeedInvalid as e:
        print(f"Invalid seed document: {e}")
        raise SystemExit(1)
    if seed is None:
        print("No seed document configured (set FAMILY_SEED_JSON or pass --file).")
        raise SystemExit(1)

    if args.check:
        with_birthdays = sum(1 for m in seed.members if m.birthday)
        print(f"OK — {len(seed.members)} members, {len(seed.activities)} activities, "
              f"{with_birthdays} with birthdays.")
        return

    async with get_db_session() as db:
        summary = await seed_family(db, seed)
    print(
        f"Seed complete — {summary['members_created']} member(s) created, "
        f"{summary['members_updated']} updated, "
        f"{summary['activities_created']} activity(ies) created."
    )
    for name in summary["created"]:
        print(f"  + {name}")
    for name in summary["updated"]:
        print(f"  ~ {name}")


if __name__ == "__main__":
    asyncio.run(main())
