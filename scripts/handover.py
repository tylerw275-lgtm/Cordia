"""Hand the assistant over to the person it was built for.

Everything created while it was being tested belongs to whoever was testing it.
On the day the owner becomes a different person, that history is sitting there
looking like hers.

    python scripts/handover.py            # say what would happen, change nothing
    python scripts/handover.py --apply    # do it

Never removes consent records or the usage ledger: the first is the compliance
evidence for a registered 10DLC campaign and the second was reconciled against a
real invoice. Both would be wrong to lose and neither is test data.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.database import get_db_session  # noqa: E402
from app.services import handover  # noqa: E402


def _mask(value: str) -> str:
    if not value:
        return "(not set)"
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = "".join(c for c in value if c.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "(set)"


async def main(apply: bool) -> int:
    async with get_db_session() as db:
        print("Configured owner")
        print(f"  phone : {_mask(settings.cordia_phone_number)}")
        print(f"  email : {_mask(settings.owner_email)}")
        print()

        check = await handover.owner_check(db)
        if check["ok"]:
            print(f"OK    The principal on file is {check.get('owner_name')}, "
                  "and matches the configuration.")
        else:
            print(f"CHECK {check.get('reason') or 'owner does not match config'}")
            print(f"      {check['fix']}")
            if not check.get("phone_matches_config", True):
                print("      phone on the principal row differs from CORDIA_PHONE_NUMBER")
            if not check.get("email_matches_config", True):
                print("      email on the principal row differs from OWNER_EMAIL")
        print()

        plan = await handover.clear_test_data(db, apply=apply)
        print(plan.describe(applied=apply))
        print()

        if not apply:
            print("Nothing was changed. Re-run with --apply to carry it out.")
            return 0

        print("Done. Test data cleared; consent records and the usage ledger kept.")
        if not check["ok"]:
            print()
            print("STILL TO DO: the owner on file does not match the configuration "
                  "above. Fix that before she texts, or she will resolve as an "
                  "anonymous owner.")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
