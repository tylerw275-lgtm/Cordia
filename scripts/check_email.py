"""Verify the Gmail email setup end-to-end without touching production.

Run after setting EMAIL_ADDRESS, EMAIL_APP_PASSWORD, and OWNER_EMAIL:
    python scripts/check_email.py

It (1) checks config, (2) logs into IMAP, and (3) sends a test email to
OWNER_EMAIL. Prints PASS/FAIL for each step. No secrets are printed.
"""
import asyncio
import imaplib
import sys

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.services import email_service  # noqa: E402


def _mask(v: str) -> str:
    if not v:
        return "(not set)"
    return v[:2] + "…" + v[-2:] if len(v) > 4 else "set"


async def main() -> None:
    print("Email configuration:")
    print(f"  provider:        {settings.email_provider}")
    print(f"  EMAIL_ADDRESS:   {settings.email_address or '(not set)'}")
    print(f"  EMAIL_APP_PASSWORD: {_mask(settings.email_app_password)}")
    print(f"  OWNER_EMAIL:     {settings.owner_email or '(not set)'}")
    print(f"  from header:     {email_service.from_address() or '(not set)'}")
    print()

    ok = True
    if settings.email_provider != "gmail":
        print("NOTE: provider is not 'gmail'; this check targets the Gmail setup.")
    if not settings.email_address or not settings.email_app_password:
        print("FAIL: EMAIL_ADDRESS and EMAIL_APP_PASSWORD must both be set.")
        return
    if settings.owner_email and settings.owner_email.strip().lower() == settings.email_address.strip().lower():
        print("FAIL: OWNER_EMAIL must differ from EMAIL_ADDRESS (the assistant would email itself).")
        ok = False

    # IMAP login
    try:
        box = imaplib.IMAP4_SSL("imap.gmail.com")
        box.login(settings.email_address, settings.email_app_password)
        box.select("INBOX")
        box.logout()
        print("PASS: IMAP login (inbox polling will work).")
    except Exception as e:
        print(f"FAIL: IMAP login — {e}")
        ok = False

    # SMTP send
    to = settings.owner_email or settings.email_address
    result = await email_service.send_email(
        to=to,
        subject="Cordia email check",
        body_markdown="## It works\nThis is a test from your assistant's email setup.",
    )
    if result.get("sent"):
        print(f"PASS: sent a test email to {to}. Check that inbox.")
    else:
        print(f"FAIL: send — {result.get('reason')}")
        ok = False

    print()
    print("ALL GOOD ✅" if ok else "Some checks failed — see above.")


if __name__ == "__main__":
    asyncio.run(main())
