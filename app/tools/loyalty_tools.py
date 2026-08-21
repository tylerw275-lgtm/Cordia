"""Loyalty programs (owner-only).

Cordia tells Cord which programs she belongs to; the numbers are encrypted at
rest and used for flight searches so fares and accrual reflect her status.
Tools here never return a full account number — only the program and last four.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import loyalty_service

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "name": "save_loyalty_program",
        "description": (
            "Record a rewards program Cordia belongs to — airline frequent flyer, hotel, or "
            "credit-card points. Airline numbers are attached to flight searches so she gets "
            "her status and earns miles. Use whenever she mentions a program or gives a "
            "number. Confirm back using ONLY the program name and last four digits; never "
            "repeat the full number. NEVER store a credit card's card number — for cards, "
            "record the rewards program only (e.g. 'Amex Membership Rewards')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "program_name": {"type": "string", "description": "e.g. 'Delta SkyMiles', 'Marriott Bonvoy', 'Amex Membership Rewards'"},
                "program_type": {
                    "type": "string",
                    "enum": ["airline", "hotel", "credit_card"],
                    "description": "airline programs get used in flight searches; hotel and credit_card inform points strategy",
                },
                "account_number": {"type": "string", "description": "Her membership number, if she gives it. Omit for credit cards unless it's a rewards member number (never a card number)."},
                "airline_iata_code": {"type": "string", "description": "Required for airline programs: DL, AA, UA, WN, AS, B6..."},
                "status_notes": {"type": "string", "description": "e.g. 'Diamond Medallion', 'transfers 1:1 to Delta and Hilton'"},
            },
            "required": ["program_name", "program_type"],
        },
    },
    {
        "name": "list_loyalty_programs",
        "description": (
            "List the rewards programs Cordia belongs to (program, type, status, whether a "
            "number is on file, last four only). Use when she asks what you have, when "
            "planning travel to leverage status or points, or before asking her for a number "
            "you may already hold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "program_type": {"type": "string", "enum": ["airline", "hotel", "credit_card"], "description": "Optional filter"},
            },
        },
    },
    {
        "name": "remove_loyalty_program",
        "description": "Delete a rewards program she no longer uses, including its stored number.",
        "input_schema": {
            "type": "object",
            "properties": {"program_name": {"type": "string"}},
            "required": ["program_name"],
        },
    },
]


async def save_loyalty_program_handler(db: AsyncSession, **kw) -> dict:
    program_type = kw.get("program_type", "airline")
    if program_type == "airline" and kw.get("account_number") and not kw.get("airline_iata_code"):
        return {
            "saved": False,
            "message": "Ask Cordia which airline this program belongs to (or supply its IATA code) so it can be used in searches.",
        }
    try:
        account = await loyalty_service.save_account(
            db,
            program_name=kw["program_name"],
            program_type=program_type,
            account_number=kw.get("account_number"),
            airline_iata_code=kw.get("airline_iata_code"),
            status_notes=kw.get("status_notes"),
        )
    except loyalty_service.EncryptionUnavailable:
        logger.error("Loyalty number rejected: LOYALTY_ENCRYPTION_KEY is not configured")
        return {
            "saved": False,
            "message": (
                "Tell Cordia you can note the program name but can't store the number "
                "securely yet, and that you've flagged it for the team to enable."
            ),
        }
    except ValueError as e:
        if str(e) == "card_number_refused":
            return {
                "saved": False,
                "message": (
                    "That looked like a credit card number. Tell her you don't store card "
                    "numbers — just the rewards program name — and save it without the number."
                ),
            }
        raise
    view = loyalty_service.safe_view(account)
    return {
        "saved": True,
        **view,
        "message": "Confirm using the program name and last four only — never repeat the full number.",
    }


async def list_loyalty_programs_handler(db: AsyncSession, **kw) -> dict:
    accounts = await loyalty_service.list_accounts(db, program_type=kw.get("program_type"))
    views = [loyalty_service.safe_view(a) for a in accounts]
    airline_ready = sum(1 for v in views if v["type"] == "airline" and v["number_on_file"])
    return {
        "count": len(views),
        "programs": views,
        "airline_numbers_applied_to_searches": airline_ready,
        "message": "No programs on file yet — ask which airline, hotel and card programs she uses." if not views else None,
    }


async def remove_loyalty_program_handler(db: AsyncSession, **kw) -> dict:
    removed = await loyalty_service.remove_account(db, kw["program_name"])
    return {"removed": removed, "program": kw["program_name"]}


HANDLERS = {
    "save_loyalty_program": save_loyalty_program_handler,
    "list_loyalty_programs": list_loyalty_programs_handler,
    "remove_loyalty_program": remove_loyalty_program_handler,
}
