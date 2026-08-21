"""Three people, three walled workspaces.

Tom and Karie each get a full Cord for their own work. Nothing of Cordia's
crosses over until she says so, and Cord never messages either of them on its
own initiative. The failure mode this guards against is quiet: Cord mentioning
to Tom a gift Cordia is planning for him, or a scheduled job cheerfully texting
everyone.
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.authorized_user import AccessGrant, AuthorizedUser
from app.models.memory import Memory
from app.services import memory_service, principal_service
from app.tools import project_tools as pt
from app.tools import sharing_tools as st

CORDIA = {"name": "Cordia", "phone": "+16155550001", "email": "cordia@example.com", "is_owner": True}
TOM = {"name": "Tom Harrington", "phone": "+16157080001", "email": "tom@example.com"}
KARIE = {"name": "Karie Hampton", "phone": "+16153101552", "email": "karie@example.com"}


@pytest_asyncio.fixture
async def people(db, mocker):
    mocker.patch.object(settings, "principals_json", json.dumps([CORDIA, TOM, KARIE]))
    await principal_service.seed_principals(db)
    return {
        "cordia": await principal_service.find_by_name(db, "Cordia"),
        "tom": await principal_service.find_by_name(db, "Tom"),
        "karie": await principal_service.find_by_name(db, "Karie"),
    }


# --- identity --------------------------------------------------------------

@pytest.mark.asyncio
async def test_seeding_creates_each_principal_once(db, people):
    assert (await principal_service.seed_principals(db)) == 0, "re-seeding duplicated people"
    assert len(await principal_service.list_principals(db)) == 3


@pytest.mark.asyncio
async def test_exactly_one_owner(db, people):
    owner = await principal_service.get_owner(db)
    assert owner.name == "Cordia"
    assert people["tom"].is_owner is False


@pytest.mark.asyncio
async def test_resolution_by_phone_ignores_formatting(db, people):
    for form in ("+16157080001", "6157080001", "(615) 708-0001"):
        assert (await principal_service.resolve_by_phone(db, form)).name == "Tom Harrington"


@pytest.mark.asyncio
async def test_resolution_by_email_is_case_insensitive(db, people):
    """Karie works mostly by email, so this path matters as much as her number."""
    assert (await principal_service.resolve_by_email(db, "KARIE@Example.com ")).name == "Karie Hampton"


@pytest.mark.asyncio
async def test_a_stranger_resolves_to_nobody(db, people):
    assert await principal_service.resolve_by_phone(db, "+17876765645") is None
    assert await principal_service.resolve_by_email(db, "spam@example.com") is None


@pytest.mark.asyncio
async def test_malformed_config_seeds_nobody_rather_than_half(db, mocker):
    mocker.patch.object(settings, "principals_json", "{not json")
    mocker.patch.object(settings, "cordia_phone_number", "")
    mocker.patch.object(settings, "owner_email", "")
    assert await principal_service.seed_principals(db) == 0


@pytest.mark.asyncio
async def test_unset_config_still_resolves_cordia(db, mocker):
    """A deployment that never sets PRINCIPALS_JSON must not reject her own number."""
    mocker.patch.object(settings, "principals_json", "")
    mocker.patch.object(settings, "cordia_phone_number", "+16155550001")
    await principal_service.seed_principals(db)
    owner = await principal_service.get_owner(db)
    assert owner is not None and owner.is_owner


# --- the wall --------------------------------------------------------------

@pytest.mark.asyncio
async def test_nothing_is_shared_by_default(db, people):
    for scope in principal_service.SHAREABLE_SCOPES:
        assert await principal_service.has_access(db, people["tom"], scope) is False
        assert await principal_service.has_access(db, people["karie"], scope) is False


@pytest.mark.asyncio
async def test_the_owner_can_always_see_her_own(db, people):
    for scope in principal_service.SHAREABLE_SCOPES:
        assert await principal_service.has_access(db, people["cordia"], scope) is True


@pytest.mark.asyncio
async def test_sharing_then_revoking(db, people):
    await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    assert await principal_service.has_access(db, people["karie"], "loyalty") is True

    await st.stop_sharing_handler(db, person="Karie", what="loyalty")
    assert await principal_service.has_access(db, people["karie"], "loyalty") is False


@pytest.mark.asyncio
async def test_revoking_keeps_the_record_of_who_could_see_it(db, people):
    await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    await st.stop_sharing_handler(db, person="Karie", what="loyalty")

    row = (await db.execute(select(AccessGrant))).scalars().one()
    assert row.revoked_at is not None, "the grant was deleted rather than closed"


@pytest.mark.asyncio
async def test_sharing_one_area_does_not_open_the_others(db, people):
    await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    assert await principal_service.has_access(db, people["karie"], "leases") is False
    assert await principal_service.has_access(db, people["karie"], "memories") is False


@pytest.mark.asyncio
async def test_sharing_with_karie_does_not_share_with_tom(db, people):
    await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    assert await principal_service.has_access(db, people["tom"], "loyalty") is False


@pytest.mark.asyncio
async def test_an_unknown_area_is_refused_rather_than_guessed(db, people):
    out = await st.share_with_handler(db, person="Tom", what="everything", acting_user=people["cordia"])
    assert out["ok"] is False and out["reason"] == "unknown_area"


@pytest.mark.asyncio
async def test_sharing_with_someone_who_does_not_use_cord(db, people):
    out = await st.share_with_handler(db, person="Aaron", what="loyalty", acting_user=people["cordia"])
    assert out["ok"] is False and out["reason"] == "unknown_person"


@pytest.mark.asyncio
async def test_sharing_twice_is_a_no_op(db, people):
    await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    again = await st.share_with_handler(db, person="Karie", what="loyalty", acting_user=people["cordia"])
    assert again["already_had_it"] is True
    assert len((await db.execute(select(AccessGrant))).scalars().all()) == 1


# --- memories --------------------------------------------------------------

async def _memories_visible_to(db, person, query):
    """Exactly what the system prompt builder does, so these assertions track
    the real read path rather than a parallel one."""
    ids, unowned = await principal_service.visible_scope(db, person, "memories")
    return await memory_service.search_memories(
        db, query=query, visible_owner_ids=ids, may_read_unowned=unowned
    )


@pytest.mark.asyncio
async def test_cordias_memories_are_invisible_to_tom(db, people):
    """The leak that matters most: memories are injected into every prompt, so
    an unfiltered search hands Tom a gift she is planning for him."""
    await memory_service.store_memory(
        db, category="fact", subject="Tom birthday gift",
        content="watch he mentioned in March", owner_user_id=people["cordia"].id,
    )

    tom_sees = await _memories_visible_to(db, people["tom"], "Tom birthday gift watch")
    assert tom_sees == []

    she_sees = await _memories_visible_to(db, people["cordia"], "Tom birthday gift watch")
    assert len(she_sees) == 1


@pytest.mark.asyncio
async def test_toms_own_memories_are_his(db, people):
    await memory_service.store_memory(
        db, category="fact", subject="golf handicap", content="twelve",
        owner_user_id=people["tom"].id,
    )
    seen = await _memories_visible_to(db, people["tom"], "golf handicap")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_memories_predating_multi_user_stay_cordias(db, people):
    """Rows with no owner were hers. Hiding them would make her own history
    vanish the moment this shipped."""
    db.add(Memory(category="fact", subject="anniversary", content="June 4"))
    await db.commit()

    hers = await _memories_visible_to(db, people["cordia"], "anniversary")
    assert len(hers) == 1

    his = await _memories_visible_to(db, people["tom"], "anniversary")
    assert his == []


@pytest.mark.asyncio
async def test_shared_memories_become_visible_and_then_not(db, people):
    await memory_service.store_memory(
        db, category="preference", subject="hotel preference",
        content="always a corner room", owner_user_id=people["cordia"].id,
    )

    async def karie_sees():
        return await _memories_visible_to(db, people["karie"], "hotel preference corner room")

    assert await karie_sees() == []
    await st.share_with_handler(db, person="Karie", what="memories", acting_user=people["cordia"])
    assert len(await karie_sees()) == 1
    await st.stop_sharing_handler(db, person="Karie", what="memories")
    assert await karie_sees() == []


# --- projects --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_project_is_invisible_to_another_principal(db, people):
    started = await pt.start_project_handler(
        db, title="Naples house", request="what should I pack for the naples house",
        acting_user=people["cordia"],
    )
    pid = started["project_id"]

    # Reported as not found, not as forbidden — "you can't see that" would
    # confirm it exists.
    assert (await pt.get_project_handler(db, project_id=pid, acting_user=people["tom"]))["found"] is False
    assert (await pt.get_project_handler(db, project_id=pid, acting_user=people["cordia"]))["found"] is True


@pytest.mark.asyncio
async def test_listing_shows_only_your_own(db, people):
    await pt.start_project_handler(db, title="Naples house", request="packing for naples",
                                   acting_user=people["cordia"])
    await pt.start_project_handler(db, title="Golf trip", request="plan a golf trip",
                                   acting_user=people["tom"])

    assert [p["title"] for p in (await pt.list_projects_handler(db, acting_user=people["tom"]))["projects"]] == ["Golf trip"]
    assert {p["title"] for p in (await pt.list_projects_handler(db, acting_user=people["cordia"]))["projects"]} == {"Naples house"}


@pytest.mark.asyncio
async def test_sharing_projects_lets_tom_pick_one_up(db, people):
    started = await pt.start_project_handler(
        db, title="Naples house", request="packing for naples", acting_user=people["cordia"])
    pid = started["project_id"]

    await st.share_with_handler(db, person="Tom", what="projects", acting_user=people["cordia"])
    assert (await pt.get_project_handler(db, project_id=pid, acting_user=people["tom"]))["found"] is True


# --- briefing is not sharing ----------------------------------------------

@pytest.mark.asyncio
async def test_briefing_sends_once_and_grants_nothing(db, people, mocker):
    """Telling Tom about the trip must not subscribe him to it. This is the
    distinction the whole design turns on."""
    send = mocker.patch("app.services.email_service.send_email",
                        new=mocker.AsyncMock(return_value={"sent": True}))
    mocker.patch("app.services.consent_service.is_approved", new=mocker.AsyncMock(return_value=False))

    out = await st.brief_person_handler(db, person="Tom", message="We leave Friday at 4.")

    assert out["sent"] is True and out["channel"] == "email"
    assert send.await_count == 1
    for scope in principal_service.SHAREABLE_SCOPES:
        assert await principal_service.has_access(db, people["tom"], scope) is False
    assert (await db.execute(select(AccessGrant))).scalars().all() == []


@pytest.mark.asyncio
async def test_briefing_texts_only_a_consented_approved_number(db, people, mocker):
    """Being set up as a principal is not the same as opting in to SMS. Cord
    still never texts anyone first."""
    sms = mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock())
    email = mocker.patch("app.services.email_service.send_email",
                         new=mocker.AsyncMock(return_value={"sent": True}))
    mocker.patch("app.services.consent_service.is_approved", new=mocker.AsyncMock(return_value=True))

    await st.brief_person_handler(db, person="Tom", message="We leave Friday.")

    assert sms.await_count == 1
    assert email.await_count == 0


@pytest.mark.asyncio
async def test_briefing_someone_unreachable_says_so(db, people, mocker):
    mocker.patch("app.services.consent_service.is_approved", new=mocker.AsyncMock(return_value=False))
    people["tom"].email = None
    await db.commit()

    out = await st.brief_person_handler(db, person="Tom", message="hi")
    assert out["sent"] is False and out["reason"] == "no_route"


# --- what Cord is told about the person in front of it ---------------------

@pytest.mark.asyncio
async def test_a_non_owner_is_told_the_boundary(db, people):
    from app.services.claude_service import _build_owner_system

    system = await _build_owner_system(db, "hello", None, sender_user=people["tom"])
    text = " ".join(b["text"] for b in system)

    assert "Tom Harrington" in text
    assert "their own workspace" in text.lower()
    assert "cordia" in text.lower()


@pytest.mark.asyncio
async def test_the_owner_is_not_told_she_is_walled_off_from_herself(db, people):
    from app.services.claude_service import _build_owner_system

    system = await _build_owner_system(db, "hello", None, sender_user=people["cordia"])
    text = " ".join(b["text"] for b in system)

    assert "account holder" in text.lower()
    assert "off limits" not in text.lower()


# --- proactive jobs stay hers ---------------------------------------------

def test_scheduled_jobs_target_cordia_only():
    """The easy well-meaning regression is "text all the principals". Morning
    briefs and birthday nudges are hers; Tom and Karie never get them."""
    import pathlib

    jobs = pathlib.Path("app/scheduler")
    source = "\n".join(f.read_text() for f in jobs.rglob("*.py"))

    assert "cordia_phone_number" in source
    for leak in ("list_principals", "authorized_users", "AuthorizedUser"):
        assert leak not in source, (
            f"a scheduled job references {leak} — proactive messages must go to "
            "Cordia only, never to every principal"
        )


# --- outbound approval is per-workspace ------------------------------------

@pytest.mark.asyncio
async def test_tom_cannot_release_a_batch_drafted_in_cordias_thread(db, people):
    """The approval check used to scan every conversation. With three
    principals that would let Tom typing a code in his own workspace send
    messages Cordia drafted."""
    from app.models.conversation import Conversation, Message
    from app.tools import outbound_tools as ot

    toms = Conversation(phone_number=people["tom"].phone)
    db.add(toms)
    await db.flush()
    db.add(Message(conversation_id=toms.id, role="user", content="send it: ABC123"))
    await db.commit()

    assert await ot._code_confirmed_by_owner(db, "ABC123", people["tom"]) is True
    assert await ot._code_confirmed_by_owner(db, "ABC123", people["cordia"]) is False


@pytest.mark.asyncio
async def test_a_principal_with_no_conversation_cannot_approve(db, people):
    from app.tools import outbound_tools as ot
    assert await ot._code_confirmed_by_owner(db, "ABC123", people["karie"]) is False


# --- adding people from the dashboard ---------------------------------------

@pytest.mark.asyncio
async def test_someone_added_by_hand_is_resolvable_both_ways(db):
    """The point of the form: composing JSON full of phone numbers into an env
    var and redeploying failed silently when it wasn't done."""
    user, outcome = await principal_service.add_principal(
        db, "Karie Hampton", "+16153101552", "Karie@Example.com"
    )

    assert outcome == "added"
    assert user.is_owner is False
    assert (await principal_service.resolve_by_phone(db, "(615) 310-1552")).name == "Karie Hampton"
    assert (await principal_service.resolve_by_email(db, "karie@example.com")).name == "Karie Hampton"


@pytest.mark.asyncio
async def test_a_person_with_no_way_to_reach_them_is_refused(db):
    """A row with neither phone nor email looks right on the page and does
    nothing: it can never be resolved from an inbound message or messaged."""
    user, outcome = await principal_service.add_principal(db, "Ghost")
    assert user is None and outcome == "no_contact_route"


@pytest.mark.asyncio
async def test_adding_an_existing_number_updates_instead_of_duplicating(db):
    """A duplicate would split their conversation history in two."""
    await principal_service.add_principal(db, "Tom", "+16157080001")
    user, outcome = await principal_service.add_principal(
        db, "Tom Harrington", "+16157080001", "tom@example.com"
    )

    assert outcome == "updated"
    assert user.name == "Tom Harrington"
    assert user.email == "tom@example.com"
    assert len(await principal_service.list_principals(db)) == 1


@pytest.mark.asyncio
async def test_the_form_cannot_create_a_second_owner(db, people):
    user, _ = await principal_service.add_principal(db, "Impostor", "+15559990000")
    assert user.is_owner is False
    assert (await principal_service.get_owner(db)).name == "Cordia"


@pytest.mark.asyncio
async def test_deactivating_stops_resolution_but_keeps_the_row(db, people):
    assert await principal_service.set_active(db, people["tom"].id, False) is True

    assert await principal_service.resolve_by_phone(db, TOM["phone"]) is None
    assert await principal_service.resolve_by_email(db, TOM["email"]) is None
    # Still on file, so their history and grants stay coherent.
    assert len(await principal_service.list_principals(db)) == 3


@pytest.mark.asyncio
async def test_the_owner_cannot_be_deactivated(db, people):
    assert await principal_service.set_active(db, people["cordia"].id, False) is False
    assert await principal_service.resolve_by_phone(db, CORDIA["phone"]) is not None


def test_config_health_reports_malformed_json(mocker):
    """Malformed PRINCIPALS_JSON otherwise only ever shows in a boot log."""
    mocker.patch.object(settings, "principals_json", "{not json")
    status, message = principal_service.config_health()
    assert status == "invalid" and "not valid JSON" in message

    mocker.patch.object(settings, "principals_json", "")
    assert principal_service.config_health()[0] == "unset"

    mocker.patch.object(settings, "principals_json", json.dumps([CORDIA, TOM]))
    status, message = principal_service.config_health()
    assert status == "ok" and "2 entries" in message


# --- through the dashboard --------------------------------------------------

@pytest.mark.asyncio
async def test_adding_through_the_dashboard_requires_a_session(db, client, mocker):
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "admin_api_secret", "s3cret")

    r = await client.post("/health/principals/add", data={"name": "Tom", "phone": "+16157080001"})

    assert r.status_code == 401
    assert await principal_service.resolve_by_phone(db, "+16157080001") is None


@pytest.mark.asyncio
async def test_adding_through_the_dashboard_works_and_shows_up(db, client, mocker):
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    client.cookies.set(d._COOKIE, d._issue_session())

    r = await client.post(
        "/health/principals/add",
        data={"name": "Karie Hampton", "phone": "6153101552", "email": "karie@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    html = (await client.get("/health/dashboard")).text
    assert "Karie Hampton" in html
    assert "(615) 310-1552" in html
    assert "nothing shared" in html  # walled off until Cordia shares something


@pytest.mark.asyncio
async def test_the_dashboard_always_explains_how_to_add_people(db, client, mocker):
    """The guidance used to be the empty-table message, and the table is never
    empty — so the one line that would have explained this never rendered."""
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "principals_json", "")
    await principal_service.add_principal(db, "Cordia", "+16155550001")

    client.cookies.set(d._COOKIE, d._issue_session())
    html = (await client.get("/health/dashboard")).text

    assert "Add anyone who should get their own assistant" in html
    assert 'action="/health/principals/add"' in html
    assert "PRINCIPALS_JSON is not set" in html


# --- someone added through the FORM gets every rule, not just the seeded path -

@pytest_asyncio.fixture
async def added_by_form(db, client, mocker):
    """Tom, created the way Tyler will actually create him — through the
    dashboard, not PRINCIPALS_JSON. The walls key off is_owner and the grants
    table, so they should not care how the row was made. Prove it rather than
    assume it."""
    from app.api import dashboard as d

    mocker.patch.object(settings, "dashboard_password", "pw")
    mocker.patch.object(settings, "principals_json", "")
    await principal_service.add_principal(db, "Cordia", "+16155550001", "cordia@example.com")
    cordia = await principal_service.find_by_name(db, "Cordia")
    cordia.is_owner = True
    await db.commit()

    client.cookies.set(d._COOKIE, d._issue_session())
    await client.post("/health/principals/add", data={
        "name": "Tom Harrington", "phone": "+16157080001", "email": "tom@example.com",
    }, follow_redirects=False)

    return {
        "cordia": cordia,
        "tom": await principal_service.find_by_name(db, "Tom"),
    }


@pytest.mark.asyncio
async def test_form_added_person_is_not_an_owner(db, added_by_form):
    assert added_by_form["tom"].is_owner is False
    assert (await principal_service.get_owner(db)).name == "Cordia"


@pytest.mark.asyncio
async def test_form_added_person_sees_nothing_of_cordias_by_default(db, added_by_form):
    for scope in principal_service.SHAREABLE_SCOPES:
        assert await principal_service.has_access(db, added_by_form["tom"], scope) is False


@pytest.mark.asyncio
async def test_form_added_person_cannot_read_cordias_memories(db, added_by_form):
    await memory_service.store_memory(
        db, category="fact", subject="Tom birthday gift",
        content="the watch he mentioned", owner_user_id=added_by_form["cordia"].id,
    )
    assert await _memories_visible_to(db, added_by_form["tom"], "Tom birthday gift watch") == []


@pytest.mark.asyncio
async def test_form_added_person_cannot_open_cordias_project(db, added_by_form):
    started = await pt.start_project_handler(
        db, title="Naples house", request="packing for naples",
        acting_user=added_by_form["cordia"],
    )
    seen = await pt.get_project_handler(
        db, project_id=started["project_id"], acting_user=added_by_form["tom"]
    )
    assert seen["found"] is False


@pytest.mark.asyncio
async def test_form_added_person_is_told_the_boundary(db, added_by_form):
    from app.services.claude_service import _build_owner_system

    system = await _build_owner_system(db, "hello", None, sender_user=added_by_form["tom"])
    text = " ".join(b["text"] for b in system).lower()

    assert "tom harrington" in text
    assert "their own workspace" in text
    assert "off limits" in text


@pytest.mark.asyncio
async def test_adding_by_form_does_not_grant_sms_consent(db, added_by_form):
    """Being set up is not consent. Cord still cannot text them first."""
    from app.services import consent_service

    assert await consent_service.is_approved(db, "+16157080001") is False
    assert await consent_service.status_for(db, "+16157080001") == "no_consent"


@pytest.mark.asyncio
async def test_sharing_still_has_to_be_deliberate_for_a_form_added_person(db, added_by_form):
    await st.share_with_handler(
        db, person="Tom", what="loyalty", acting_user=added_by_form["cordia"]
    )
    assert await principal_service.has_access(db, added_by_form["tom"], "loyalty") is True
    # And only that one area.
    assert await principal_service.has_access(db, added_by_form["tom"], "memories") is False
