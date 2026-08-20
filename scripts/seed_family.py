"""Load Cordia's family data into the database by hand.

The app also does this on every boot (see app.main.lifespan), so this script is
only needed for a one-off load against a database the app isn't running against:

    python scripts/seed_family.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.database import get_db_session
from app.services.family_seed import seed_family


async def main() -> None:
    async with get_db_session() as db:
        summary = await seed_family(db)
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
