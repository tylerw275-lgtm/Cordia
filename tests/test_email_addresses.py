"""`Cordia <Tyler@ai-genpartners.com>` and `tyler@ai-genpartners.com` are the
same mailbox.

Comparing the raw strings says otherwise, and that is not hypothetical. That is
what OWNER_EMAIL was set to in production, so every reply Cordia sent resolved
to `unknown` and was dropped without a word — while outbound kept working the
whole time, because a display name in a `To:` header is perfectly valid. It
presented as "it emails me but never replies to me" and took four rounds to find,
because the webhook answered 200 the entire time.

The same shape as the four phone normalisers this codebase already collapsed
into one. An address arrives from a config variable, a webhook payload, a
dashboard form or a From header, and only one of those is reliably bare.
"""
import pytest

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.contact import Contact
from app.models.family import FamilyMember
from app.services import access, email_inbound, family_circle_service, principal_service
from app.utils.email_address import emails_match, normalize_email

REAL = "tyler@ai-genpartners.com"


@pytest.mark.parametrize("written", [
    "tyler@ai-genpartners.com",
    "Tyler@Ai-GenPartners.com",
    "Cordia <Tyler@ai-genpartners.com>",
    '"Tyler Wilkinson" <tyler@ai-genpartners.com>',
    " tyler@ai-genpartners.com ",
    "<tyler@ai-genpartners.com>",
    {"email": "tyler@ai-genpartners.com"},
    {"address": "Tyler <tyler@ai-genpartners.com>"},
    ["tyler@ai-genpartners.com"],
])
def test_every_shape_an_address_arrives_in_reduces_to_the_mailbox(written):
    assert normalize_email(written) == REAL


@pytest.mark.parametrize("junk", ["", None, "Cordia", "not an address", {}, []])
def test_something_that_is_not_an_address_is_not_invented(junk):
    assert normalize_email(junk) == ""


def test_two_different_mailboxes_do_not_match():
    assert not emails_match(REAL, "cord@mail.aigenpartners.com")
    assert not emails_match("Cord <cord@mail.aigenpartners.com>", REAL)


def test_nothing_matches_nothing():
    """Or every unparseable sender would resolve to whoever has a blank
    address on file."""
    assert not emails_match("", "")
    assert not emails_match(None, None)
    assert not emails_match("Cordia", "Tom")


# --- the production bug, at each place it could have bitten -----------------

@pytest.mark.parametrize("owner_setting", [
    "Cordia <Tyler@ai-genpartners.com>",
    "tyler@ai-genpartners.com",
    " Tyler@Ai-GenPartners.com ",
])
@pytest.mark.asyncio
async def test_she_is_recognised_however_owner_email_was_typed(db, mocker, owner_setting):
    """This is the one that was live: the display-name form dropped every reply
    she sent, silently, with the webhook reporting 200."""
    mocker.patch.object(settings, "owner_email", owner_setting)
    assert (await access.resolve(db, email=REAL)).role == "owner"


@pytest.mark.asyncio
async def test_a_genuinely_different_address_is_still_unknown(db, mocker):
    """The fix must not turn into 'anything with an @ is the owner'."""
    mocker.patch.object(settings, "owner_email", "Cordia <Tyler@ai-genpartners.com>")
    assert (await access.resolve(db, email="stranger@example.com")).role == "unknown"
    assert (await access.resolve(db, email="cord@mail.aigenpartners.com")).role == "unknown"


@pytest.mark.asyncio
async def test_a_principal_added_with_a_display_name_still_resolves(db):
    """The dashboard form takes free text, so this is a matter of time."""
    db.add(AuthorizedUser(name="Karie Hampton", email="Karie Hampton <karie@example.com>"))
    await db.commit()

    found = await principal_service.resolve_by_email(db, "karie@example.com")
    assert found is not None and found.name == "Karie Hampton"


@pytest.mark.asyncio
async def test_a_relative_added_with_a_display_name_still_resolves(db):
    db.add(FamilyMember(name="Sarah Harrington", relationship="daughter",
                        has_circle_access=True, email="Sarah <sarah@example.com>"))
    await db.commit()

    found = await family_circle_service.resolve_member_by_email(db, "sarah@example.com")
    assert found is not None and found.name == "Sarah Harrington"


@pytest.mark.asyncio
async def test_a_trusted_contact_with_a_display_name_is_still_captured(db, mocker):
    """This one compared in SQL with func.lower(), which never matches a stored
    display-name form."""
    db.add(Contact(name="Marguerite Blain", trusted_inbound=True,
                   email="Marguerite <marguerite@wayfarer.example>"))
    await db.commit()
    mocker.patch.object(settings, "owner_email", "cordia@example.com")

    role, contact, _ = await email_inbound._resolve_sender(db, "marguerite@wayfarer.example")
    assert role == "trusted_contact"
    assert contact.name == "Marguerite Blain"


@pytest.mark.asyncio
async def test_the_reply_she_actually_sent_now_gets_answered(db, mocker):
    """End to end, with the exact production OWNER_EMAIL and a real Gmail reply."""
    mocker.patch.object(settings, "owner_email", "Cordia <Tyler@ai-genpartners.com>")
    mocker.patch.object(settings, "cordia_phone_number", "+16157080002")
    mocker.patch("app.services.usage_service.record_email", new=mocker.AsyncMock())
    chat = mocker.patch("app.services.claude_service.chat",
                        new=mocker.AsyncMock(return_value="On it."))
    send = mocker.patch("app.services.email_service.send_email",
                        new=mocker.AsyncMock(return_value={"sent": True}))

    status = await email_inbound.process_inbound_email(
        db, '"Tyler Wilkinson" <tyler@ai-genpartners.com>',
        "Re: Tom's NYC Day Trip",
        "Book the earlier one.\n\nOn Fri, Aug 21, 2026 at 10:04 PM Tyler wrote:\n"
        "> Here are the options\n",
    )

    assert status == "replied_as_owner"
    assert chat.called
    # Replied to the bare mailbox: the From header's display name is hers to
    # show, not ours to echo back into a To.
    assert send.await_args.kwargs["to"] == REAL


def test_extract_email_and_normalize_email_are_the_same_function():
    """Two regexes for one job is how they drift apart."""
    assert email_inbound.extract_email is normalize_email or \
        email_inbound.extract_email("A <a@b.co>") == normalize_email("A <a@b.co>")
