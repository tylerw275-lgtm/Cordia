"""Making the deployment hers.

Everything built while an assistant is being tested belongs to whoever was
testing it. On the day the owner becomes a different person, that history is
sitting there looking like hers — and worse, the principal row still carries
the tester's phone and email, so she misses the principal lookup entirely and
resolves as an anonymous owner through the config fallback. That path applies
no visibility scoping, so she would be shown all of it.
"""
import pytest

from app.config import settings
from app.models.authorized_user import AuthorizedUser
from app.models.conversation import Conversation, Message
from app.models.family import FamilyMember
from app.models.memory import Memory
from app.models.project import Project
from app.models.task import FamilyTask
from app.services import handover, principal_service

TESTER_PHONE = "+16157080002"
TESTER_EMAIL = "tyler@ai-genpartners.com"
HERS_PHONE = "+16155550100"
HERS_EMAIL = "cordia@crownbakeries.example"


async def _test_data(db):
    convo = Conversation(phone_number=TESTER_PHONE)
    db.add(convo)
    await db.commit()
    db.add_all([
        Message(conversation_id=convo.id, role="user", content="what should I pack"),
        Project(title="Naples packing", kind="place_setup", status="intake", brief=[]),
        Memory(category="fact", subject="test", content="written while testing"),
        FamilyTask(title="Renew passport"),
    ])
    await db.commit()


# --- the identity swap ------------------------------------------------------

@pytest.mark.asyncio
async def test_changing_the_configured_owner_moves_the_principal_row(db, mocker):
    """It used to gap-fill only, so the row kept the tester's details and she
    silently resolved as somebody else."""
    db.add(AuthorizedUser(name="Cordia", phone=TESTER_PHONE, email=TESTER_EMAIL,
                          is_owner=True))
    await db.commit()

    mocker.patch.object(settings, "principals_json", "")
    mocker.patch.object(settings, "cordia_phone_number", HERS_PHONE)
    mocker.patch.object(settings, "owner_email", HERS_EMAIL)

    await principal_service.seed_principals(db)

    owner = await principal_service.get_owner(db)
    assert owner.phone == HERS_PHONE
    assert owner.email == HERS_EMAIL


@pytest.mark.asyncio
async def test_a_non_owner_is_still_only_gap_filled(db, mocker):
    """Tom's details are his, not the deployment's configuration. Config must
    not overwrite what was entered by hand for him."""
    db.add(AuthorizedUser(name="Tom Harrington", phone="+16157080001",
                          email="tom@example.com"))
    await db.commit()

    mocker.patch.object(settings, "principals_json",
                        '[{"name": "Tom Harrington", "phone": "+19999999999"}]')
    await principal_service.seed_principals(db)

    tom = await principal_service.find_by_name(db, "Tom Harrington")
    assert tom.phone == "+16157080001", "config clobbered a hand-entered number"


@pytest.mark.asyncio
async def test_the_check_notices_when_the_owner_does_not_match_config(db, mocker):
    db.add(AuthorizedUser(name="Cordia", phone=TESTER_PHONE, email=TESTER_EMAIL,
                          is_owner=True))
    await db.commit()
    mocker.patch.object(settings, "cordia_phone_number", HERS_PHONE)
    mocker.patch.object(settings, "owner_email", HERS_EMAIL)

    check = await handover.owner_check(db)
    assert check["ok"] is False
    assert check["phone_matches_config"] is False
    assert check["email_matches_config"] is False
    assert check["fix"]


@pytest.mark.asyncio
async def test_the_check_passes_once_it_is_hers(db, mocker):
    db.add(AuthorizedUser(name="Cordia Harrington", phone=HERS_PHONE, email=HERS_EMAIL,
                          is_owner=True))
    await db.commit()
    mocker.patch.object(settings, "cordia_phone_number", HERS_PHONE)
    mocker.patch.object(settings, "owner_email", HERS_EMAIL)

    check = await handover.owner_check(db)
    assert check["ok"] is True


@pytest.mark.asyncio
async def test_a_display_name_in_config_still_matches(db, mocker):
    """The check must not report a mismatch over formatting."""
    db.add(AuthorizedUser(name="Cordia", phone=HERS_PHONE, email=HERS_EMAIL, is_owner=True))
    await db.commit()
    mocker.patch.object(settings, "cordia_phone_number", "(615) 555-0100")
    mocker.patch.object(settings, "owner_email", f"Cordia <{HERS_EMAIL}>")

    assert (await handover.owner_check(db))["ok"] is True


@pytest.mark.asyncio
async def test_no_owner_on_file_is_reported_rather_than_crashing(db):
    check = await handover.owner_check(db)
    assert check["ok"] is False
    assert check["reason"] == "no_owner_on_file"


# --- clearing the test data -------------------------------------------------

@pytest.mark.asyncio
async def test_a_survey_changes_nothing(db):
    """A handover happens once and is not undoable, so the default is a
    description."""
    from sqlalchemy import func, select

    await _test_data(db)
    plan = await handover.clear_test_data(db, apply=False)

    assert plan.projects == 1 and plan.memories == 1 and plan.tasks == 1
    assert (await db.execute(select(func.count(Project.id)))).scalar() == 1
    assert (await db.execute(select(func.count(Memory.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_applying_clears_the_working_history(db):
    from sqlalchemy import func, select

    await _test_data(db)
    await handover.clear_test_data(db, apply=True)

    for model in (Project, Memory, FamilyTask, Conversation, Message):
        assert (await db.execute(select(func.count(model.id)))).scalar() == 0, model


@pytest.mark.asyncio
async def test_consent_records_are_never_touched(db):
    """They are the compliance evidence for a registered 10DLC campaign. They
    are not test data even when they were created during testing."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status) "
             "VALUES (:p, :ts, 'web_form', 'approved')"),
        {"p": TESTER_PHONE, "ts": datetime.now(timezone.utc)},
    )
    await db.commit()
    await _test_data(db)

    await handover.clear_test_data(db, apply=True)

    remaining = (await db.execute(text("SELECT count(*) FROM sms_consent"))).scalar()
    assert remaining == 1, "handover deleted consent evidence"


@pytest.mark.asyncio
async def test_the_usage_ledger_is_never_touched(db):
    """It was reconciled against a real invoice; wiping it makes the spend
    figures lie."""
    from sqlalchemy import func, select

    from app.models.usage import UsageEvent
    from app.services import usage_service

    await usage_service.record(db, "sms_out", actor=TESTER_PHONE, cost_usd=0.011)
    await db.commit()
    await _test_data(db)

    await handover.clear_test_data(db, apply=True)

    assert (await db.execute(select(func.count(UsageEvent.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_the_family_roster_survives(db):
    """It is hers already, and it holds real people."""
    from sqlalchemy import func, select

    db.add(FamilyMember(name="Bea Harrington", relationship="granddaughter"))
    await db.commit()
    await _test_data(db)

    await handover.clear_test_data(db, apply=True)

    assert (await db.execute(select(func.count(FamilyMember.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_principals_survive(db):
    """Renamed on handover, never dropped — removing them would take their
    consent and access history with them."""
    from sqlalchemy import func, select

    db.add(AuthorizedUser(name="Tom Harrington", phone="+16157080001"))
    await db.commit()
    await _test_data(db)

    await handover.clear_test_data(db, apply=True)

    assert (await db.execute(select(func.count(AuthorizedUser.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_an_empty_deployment_says_so_rather_than_reporting_work(db):
    plan = await handover.clear_test_data(db, apply=False)
    assert plan.total == 0
    assert "Nothing to clear" in plan.describe()


def test_the_preserved_list_is_stated_not_implied():
    """So a future edit has to argue with the list rather than quietly widen
    the delete."""
    for table in ("sms_consent", "consent_submissions", "usage_events"):
        assert table in handover.PRESERVED


# --- editing a principal ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_principals_details_can_be_corrected(db):
    """Adding and removing were possible; changing was not, so a mistyped
    number meant a second row and a conversation split in two."""
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add(tom)
    await db.commit()

    updated, outcome = await principal_service.update_principal(
        db, tom.id, phone="+16157080009", email="tom@example.com")

    assert outcome == "updated"
    assert updated.phone == "+16157080009"
    assert updated.email == "tom@example.com"


@pytest.mark.asyncio
async def test_an_edit_cannot_leave_someone_unreachable(db):
    """A row with neither route looks fine on the page and does nothing."""
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add(tom)
    await db.commit()

    _, outcome = await principal_service.update_principal(db, tom.id, phone="", email="")
    assert outcome == "no_contact_route"
    assert (await principal_service.find_by_name(db, "Tom")).phone == "+16157080001"


@pytest.mark.asyncio
async def test_two_principals_cannot_share_a_route(db):
    """Inbound resolution would pick one of them arbitrarily."""
    cordia = AuthorizedUser(name="Cordia", phone=HERS_PHONE, is_owner=True)
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add_all([cordia, tom])
    await db.commit()

    _, outcome = await principal_service.update_principal(db, tom.id, phone=HERS_PHONE)
    assert outcome == "already_taken"


@pytest.mark.asyncio
async def test_an_email_is_normalised_on_the_way_in(db):
    """The dashboard form is free text, and a display name there is what broke
    inbound email for a day."""
    tom = AuthorizedUser(name="Tom Harrington", phone="+16157080001")
    db.add(tom)
    await db.commit()

    updated, _ = await principal_service.update_principal(
        db, tom.id, email="Tom Harrington <Tom@Example.com>")
    assert updated.email == "tom@example.com"


@pytest.mark.asyncio
async def test_editing_somebody_who_is_gone_is_reported(db):
    import uuid

    _, outcome = await principal_service.update_principal(db, uuid.uuid4(), name="X")
    assert outcome == "unknown_principal"


def test_the_report_is_past_tense_once_it_has_happened():
    """A script that says "would remove" after removing reads as though it did
    nothing — the wrong thing to believe at the one moment this is run."""
    plan = handover.Plan(projects=2, memories=1, conversations=1, messages=9)
    assert plan.describe(applied=False).startswith("Would remove")
    assert plan.describe(applied=True).startswith("Removed")
    assert "Kept, untouched" in plan.describe(applied=True)
