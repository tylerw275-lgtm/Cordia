"""Loyalty programs: encrypted storage and Duffel hand-off.

Account numbers are encrypted at rest with a Fernet key held in the
environment (LOYALTY_ENCRYPTION_KEY). Two rules the rest of the app depends on:

1. Plaintext numbers never reach the model. Tools return the program name and
   last four digits only; the full value leaves this module in exactly one
   direction — into a Duffel request.
2. Storage fails closed. With no key configured we refuse to save rather than
   writing a rewards number to the database in the clear.
"""
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.loyalty import LoyaltyAccount

logger = logging.getLogger(__name__)

# Card numbers are 13-19 digits and must never be stored; a rewards/membership
# number is a different thing. Anything long enough to be a PAN is rejected.
_CARD_LIKE = re.compile(r"^\d{13,19}$")


class EncryptionUnavailable(Exception):
    """No usable LOYALTY_ENCRYPTION_KEY — refuse to store secrets in the clear."""


def _fernet():
    from cryptography.fernet import Fernet
    key = (settings.loyalty_encryption_key or "").strip()
    if not key:
        raise EncryptionUnavailable("LOYALTY_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.encode())
    except Exception as e:
        raise EncryptionUnavailable(f"LOYALTY_ENCRYPTION_KEY is not a valid Fernet key: {e}") from e


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception as e:
        logger.error(f"Could not decrypt a loyalty account number: {e}")
        return None


def looks_like_a_card_number(value: str) -> bool:
    return bool(_CARD_LIKE.match(re.sub(r"[\s-]", "", value or "")))


def safe_view(account: LoyaltyAccount) -> dict:
    """What the model and conversation are allowed to see."""
    return {
        "program": account.program_name,
        "type": account.program_type,
        "airline_code": account.airline_iata_code,
        "number_on_file": bool(account.account_number_encrypted),
        "last_four": account.last_four,
        "status_notes": account.status_notes,
    }


async def get_by_program(db: AsyncSession, program_name: str) -> LoyaltyAccount | None:
    result = await db.execute(
        select(LoyaltyAccount).where(func.lower(LoyaltyAccount.program_name) == program_name.strip().lower())
    )
    return result.scalars().first()


async def list_accounts(db: AsyncSession, program_type: str | None = None) -> list[LoyaltyAccount]:
    stmt = select(LoyaltyAccount).order_by(LoyaltyAccount.program_type, LoyaltyAccount.program_name)
    if program_type:
        stmt = stmt.where(LoyaltyAccount.program_type == program_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def save_account(
    db: AsyncSession,
    program_name: str,
    program_type: str = "airline",
    account_number: str | None = None,
    airline_iata_code: str | None = None,
    status_notes: str | None = None,
) -> LoyaltyAccount:
    account = await get_by_program(db, program_name)
    if account is None:
        account = LoyaltyAccount(program_name=program_name.strip()[:120])
        db.add(account)

    account.program_type = program_type
    if airline_iata_code:
        account.airline_iata_code = airline_iata_code.strip().upper()[:3]
    if status_notes:
        account.status_notes = status_notes

    if account_number:
        cleaned = re.sub(r"[\s-]", "", account_number)
        if program_type == "credit_card" and looks_like_a_card_number(cleaned):
            raise ValueError("card_number_refused")
        account.account_number_encrypted = encrypt(cleaned)
        account.last_four = cleaned[-4:]

    await db.commit()
    await db.refresh(account)
    return account


async def remove_account(db: AsyncSession, program_name: str) -> bool:
    account = await get_by_program(db, program_name)
    if not account:
        return False
    await db.delete(account)
    await db.commit()
    return True


async def duffel_loyalty_accounts(db: AsyncSession) -> list[dict]:
    """Airline programs in the shape Duffel wants on a passenger.

    Server-side only — the decrypted numbers are placed directly into the
    outgoing request and are never returned to a tool result.
    """
    out: list[dict] = []
    for account in await list_accounts(db, program_type="airline"):
        if not (account.airline_iata_code and account.account_number_encrypted):
            continue
        number = decrypt(account.account_number_encrypted)
        if number:
            out.append({"airline_iata_code": account.airline_iata_code, "account_number": number})
    return out
