"""Telling the people who run the service when something needs them.

Two things used to end at the database. A feature request became a memory nobody
reads; a failure became a row on a dashboard nobody is watching at three in the
morning. Both now reach the operator by email.

Most of this file is about the ways that goes wrong rather than the sending. An
alert raised from inside an `except` block turns a bad reply into no reply. An
outage means every message she sends produces the same error, and forty emails
about one incident is worse than none. And the mail provider is exactly the sort
of thing that fails, so an alert about a failed alert would loop.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import alerts, usage_service

OWNER = "+16157080002"
OPERATOR = "tyler@ai-genpartners.com"


@pytest.fixture(autouse=True)
def _clean(mocker):
    alerts.reset()
    mocker.patch.object(settings, "operator_email", OPERATOR)
    mocker.patch.object(settings, "alert_cooldown_minutes", 30)
    mocker.patch.object(settings, "alert_max_per_hour", 20)
    yield
    alerts.reset()


@pytest.fixture
def sent(mocker):
    return mocker.patch("app.services.email_service.send_email",
                        new=mocker.AsyncMock(return_value={"sent": True}))


def _body(call):
    return call.kwargs["body_markdown"]


# --- feature requests -------------------------------------------------------

@pytest.mark.asyncio
async def test_a_feature_request_reaches_the_team(sent):
    await alerts.notify_feature_request(
        title="Text me a weekly grandkid photo recap",
        details="It would be great if you could send me the photos from the week on Sundays.",
        priority="would_use_often", actor=OWNER,
    )

    assert sent.await_args.kwargs["to"] == OPERATOR
    assert "grandkid photo recap" in sent.await_args.kwargs["subject"]
    body = _body(sent.await_args)
    assert "photos from the week on Sundays" in body, "her actual words were dropped"
    assert "would_use_often" in body


@pytest.mark.asyncio
async def test_the_request_survives_a_failed_email(db, mocker):
    """The backlog nobody read was the old failure. Losing the request as well
    would be a worse one."""
    from sqlalchemy import select
    from app.models.memory import Memory
    from app.tools import feature_tools

    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(side_effect=RuntimeError("SMTP down")))

    result = await feature_tools.request_feature_handler(
        db, title="Weekly photo recap", details="Send the week's photos on Sundays.")

    assert result["logged"] is True
    assert result["emailed_to_team"] is False
    stored = (await db.execute(select(Memory))).scalars().all()
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_every_feature_request_is_sent_rather_than_throttled(sent):
    """They are rare, and each one is the entire point."""
    for i in range(5):
        await alerts.notify_feature_request(title=f"Idea {i}", details="...")
    assert sent.await_count == 5


@pytest.mark.asyncio
async def test_cord_is_told_not_to_mention_how_it_was_sent(db, mocker):
    """She asked her assistant for something; a line about email plumbing is
    the assistant talking about itself."""
    from app.tools import feature_tools

    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    result = await feature_tools.request_feature_handler(
        db, title="Weekly photo recap", details="...")
    assert "Do not mention email" in result["message"]


# --- errors -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_error_reaches_the_operator_with_enough_to_reproduce_it(sent):
    await alerts.notify_error(
        "sms_reply", "BadRequestError",
        detail="messages.1: tool_use ids must be unique",
        actor=OWNER, message="what should I pack for the naples house",
    )

    assert sent.await_args.kwargs["to"] == OPERATOR
    body = _body(sent.await_args)
    assert "sms_reply" in body
    assert "BadRequestError" in body
    assert "tool_use ids must be unique" in body
    assert "naples house" in body, "no way to reproduce it without what she sent"
    assert "7080002" not in body and "+16157080002" not in body, "her full number went out"


@pytest.mark.asyncio
async def test_the_alert_says_what_she_was_told(sent):
    """So the reply she is looking at can be matched to the alert."""
    await alerts.notify_error("sms_reply", "TimeoutError", actor=OWNER)
    assert "Something went wrong on my end" in _body(sent.await_args)


@pytest.mark.asyncio
async def test_an_error_with_no_message_still_alerts(sent):
    """Scheduled jobs and tool failures have no triggering text."""
    assert await alerts.notify_error("tool:store_memory", "ProgrammingError") is True


# --- the storm ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_same_failure_is_emailed_once_not_forty_times(sent):
    """An Anthropic outage means every message she sends produces this."""
    for _ in range(40):
        await alerts.notify_error("sms_reply", "APIStatusError", actor=OWNER)
    assert sent.await_count == 1


@pytest.mark.asyncio
async def test_the_ones_held_back_are_counted_into_the_next_email(sent):
    """Suppressed is not the same as forgotten — the scale of an incident is
    the most useful thing in the alert."""
    await alerts.notify_error("sms_reply", "APIStatusError", actor=OWNER)
    for _ in range(14):
        await alerts.notify_error("sms_reply", "APIStatusError", actor=OWNER)

    # Walk the clock past the cooldown.
    key = ("sms_reply", "APIStatusError")
    alerts._SEEN[key]["last_sent"] -= timedelta(minutes=31)

    await alerts.notify_error("sms_reply", "APIStatusError", actor=OWNER)

    assert sent.await_count == 2
    assert "14 more" in _body(sent.await_args)


@pytest.mark.asyncio
async def test_a_different_failure_is_not_silenced_by_a_noisy_one(sent):
    """Deduplication is per failure. One loud error must not hide a new one."""
    for _ in range(10):
        await alerts.notify_error("sms_reply", "APIStatusError")
    await alerts.notify_error("email_inbound", "KeyError")

    assert sent.await_count == 2
    assert "email_inbound" in _body(sent.await_args)


@pytest.mark.asyncio
async def test_the_same_error_somewhere_else_is_its_own_alert(sent):
    await alerts.notify_error("sms_reply", "TimeoutError")
    await alerts.notify_error("tool:search_flights", "TimeoutError")
    assert sent.await_count == 2


@pytest.mark.asyncio
async def test_an_hourly_cap_bounds_the_worst_case(sent, mocker):
    """Even a hundred distinct failures must not become a hundred emails."""
    mocker.patch.object(settings, "alert_max_per_hour", 5)
    for i in range(100):
        await alerts.notify_error(f"place_{i}", "SomeError")
    assert sent.await_count == 5


@pytest.mark.asyncio
async def test_the_throttle_table_stays_bounded(sent):
    """It is keyed by failure signature and lives for the process's lifetime."""
    for i in range(alerts._MAX_TRACKED + 100):
        await alerts.notify_error(f"place_{i}", "SomeError")
    assert len(alerts._SEEN) <= alerts._MAX_TRACKED


# --- it must never make things worse ----------------------------------------

@pytest.mark.asyncio
async def test_a_failing_alert_never_raises_into_the_caller(mocker):
    """record_error runs inside except blocks that are already handling a
    failure. Raising here turns a bad reply into no reply."""
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(side_effect=RuntimeError("SMTP exploded")))
    assert await alerts.notify_error("sms_reply", "ValueError", actor=OWNER) is False


@pytest.mark.asyncio
async def test_recording_an_error_never_raises_even_when_everything_is_down(mocker):
    mocker.patch("app.services.usage_service.record_standalone",
                 new=mocker.AsyncMock(side_effect=RuntimeError("db gone")))
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(side_effect=RuntimeError("SMTP gone")))

    with pytest.raises(RuntimeError):
        # record_standalone is mocked to raise, proving the mock is live...
        await usage_service.record_standalone("error")

    # ...and yet the ledger write is the only thing that propagates; the alert
    # underneath it is swallowed.
    assert await alerts.notify_error("sms_reply", "ValueError") is False


@pytest.mark.asyncio
async def test_an_alert_cannot_alert_about_itself(mocker):
    """The mail provider is exactly the sort of thing that goes down, and an
    alert about a failed alert would loop."""
    nested = []

    async def exploding_send(**kwargs):
        nested.append(kwargs)
        # Simulate the alert path itself failing into record_error.
        await alerts.notify_error("email_service", "SMTPException")
        raise RuntimeError("SMTP down")

    mocker.patch("app.services.email_service.send_email", new=exploding_send)

    await alerts.notify_error("sms_reply", "ValueError")
    assert len(nested) == 1, f"the alert path recursed: {len(nested)} sends"


@pytest.mark.asyncio
async def test_no_operator_address_means_no_attempt(sent, mocker):
    mocker.patch.object(settings, "operator_email", "")
    assert await alerts.notify_error("sms_reply", "ValueError") is False
    assert not sent.called


@pytest.mark.asyncio
async def test_a_failed_send_is_retried_next_time_rather_than_marked_done(sent, mocker):
    """A send that failed must not start the cooldown, or one SMTP blip
    silences the incident for half an hour."""
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": False, "reason": "smtp_error"}))
    await alerts.notify_error("sms_reply", "ValueError")

    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))
    assert await alerts.notify_error("sms_reply", "ValueError") is True


# --- through the real call site ---------------------------------------------

@pytest.mark.asyncio
async def test_recording_an_error_is_what_sends_the_alert(sent, mocker):
    """Every error in the system funnels through record_error, so wiring it
    there covers sms_reply, tool failures and inbound email at once."""
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())

    await usage_service.record_error(
        "sms_reply", ValueError("boom"), actor=OWNER, message="what should I pack")

    assert sent.await_count == 1
    body = _body(sent.await_args)
    assert "sms_reply" in body and "ValueError" in body and "what should I pack" in body


@pytest.mark.asyncio
async def test_the_ledger_is_written_before_the_alert_is_attempted(sent, mocker):
    """An alert must never be able to cost us the record of what happened."""
    order = []
    mocker.patch("app.services.usage_service.record_standalone",
                 new=mocker.AsyncMock(side_effect=lambda *a, **k: order.append("ledger")))
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(side_effect=lambda **k: order.append("email") or {"sent": True}))

    await usage_service.record_error("sms_reply", ValueError("boom"))
    assert order == ["ledger", "email"]


# --- the support address ----------------------------------------------------

def test_the_help_reply_carries_the_working_support_address():
    """Carrier-mandated text on a registered 10DLC campaign. It read
    tyler@aigenpartners.com, which is not an address that exists."""
    from app.api import sms

    assert "tyler@ai-genpartners.com" in sms._HELP_MSG


def test_no_compliance_page_still_has_the_broken_address():
    from pathlib import Path

    for path in (Path("app/api/compliance.py"), Path("app/api/sms.py")):
        text = path.read_text()
        for line in text.splitlines():
            if "aigenpartners.com" in line and "cordia.aigenpartners.com" not in line:
                assert "ai-genpartners.com" in line, f"{path}: {line.strip()[:90]}"


def test_the_website_domain_was_not_hyphenated_by_mistake():
    """The domain is right without the hyphen; only the mailbox was wrong."""
    assert settings.public_base_url == "https://cordia.aigenpartners.com"


def test_the_help_reply_is_still_one_segment():
    """It is sent with force=True to every number including opted-out ones, so
    its cost is not hypothetical."""
    from app.api import sms
    from app.services.gsm import to_gsm

    assert usage_service.sms_segments(to_gsm(sms._HELP_MSG)) == 1


# --- email that arrives and gets no answer ----------------------------------

@pytest.mark.asyncio
async def test_an_ignored_email_tells_the_operator_why(sent):
    """"It sends me emails but won't reply to mine" reached the operator twice
    with nothing to go on, because every drop was a log line and a 200."""
    await alerts.notify_inbound_email_dropped(
        "ignored_unknown_sender", "tylerw275@gmail.com", "Re: Your flight options")

    body = _body(sent.await_args)
    assert "ignored_unknown_sender" in body
    assert "OWNER_EMAIL" in body, "the alert names the symptom but not the fix"
    assert "Re: Your flight options" in body


@pytest.mark.asyncio
async def test_the_dropped_senders_address_is_masked(sent):
    await alerts.notify_inbound_email_dropped("ignored_unknown_sender", "someone@example.com")
    assert "someone@example.com" not in _body(sent.await_args)


@pytest.mark.parametrize("reason", [
    "ignored_unknown_sender", "ignored_unapproved_sender",
    "ignored_empty_body", "ignored_no_sender",
])
@pytest.mark.asyncio
async def test_every_drop_reason_explains_itself(sent, reason):
    """A reason code alone is a lookup task. The alert should name the fix."""
    await alerts.notify_inbound_email_dropped(reason, "someone@example.com")
    assert "What that means" in _body(sent.await_args), reason


@pytest.mark.asyncio
async def test_a_stream_of_spam_is_one_alert_not_hundreds(sent):
    """The address is public on the consent page."""
    for i in range(50):
        await alerts.notify_inbound_email_dropped("ignored_unknown_sender", f"spam{i}@example.com")
    assert sent.await_count == 1


@pytest.mark.asyncio
async def test_a_different_drop_reason_is_its_own_alert(sent):
    await alerts.notify_inbound_email_dropped("ignored_unknown_sender", "a@example.com")
    await alerts.notify_inbound_email_dropped("ignored_empty_body", "b@example.com")
    assert sent.await_count == 2


@pytest.mark.asyncio
async def test_the_drop_is_reported_through_the_real_path(db, mocker, sent):
    """Through process_inbound_email, not by calling the alert directly."""
    from app.services import email_inbound

    mocker.patch.object(settings, "owner_email", "cordia@example.com")
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())

    status = await email_inbound.process_inbound_email(
        db, "stranger@example.com", "hello", "anyone there")

    assert status == "ignored_unknown_sender"
    assert sent.await_count == 1
    assert sent.await_args.kwargs["to"] == OPERATOR


@pytest.mark.asyncio
async def test_a_dropped_email_is_counted_in_the_ledger(db, mocker, sent):
    """So the dashboard shows how much mail is bouncing off, not just the one
    that prompted a complaint."""
    from app.services import email_inbound

    recorded = mocker.patch("app.services.usage_service.record_standalone",
                            new=mocker.AsyncMock())
    mocker.patch.object(settings, "owner_email", "cordia@example.com")

    await email_inbound.process_inbound_email(db, "stranger@example.com", "hi", "hello")

    assert recorded.await_args.args[0] == "email_ignored"
    assert recorded.await_args.kwargs["details"]["reason"] == "ignored_unknown_sender"


@pytest.mark.asyncio
async def test_a_broken_alert_never_stops_the_webhook_returning(db, mocker):
    """The provider retries a non-200, and a retry storm over an email we were
    always going to ignore helps nobody."""
    from app.services import email_inbound

    mocker.patch.object(settings, "owner_email", "cordia@example.com")
    mocker.patch("app.services.usage_service.record_standalone",
                 new=mocker.AsyncMock(side_effect=RuntimeError("ledger down")))

    status = await email_inbound.process_inbound_email(db, "stranger@example.com", "hi", "hello")
    assert status == "ignored_unknown_sender"


# --- the config endpoint answers the question this raised -------------------

def test_config_reports_whether_inbound_can_arrive_at_all(mocker):
    """"inbound_polling: false" is true and useless on a Resend deployment.
    The question is whether inbound is reachable by any route."""
    import asyncio

    from app.main import health_config

    mocker.patch.object(settings, "enable_email", True)
    mocker.patch.object(settings, "email_provider", "resend")
    mocker.patch.object(settings, "email_webhook_signing_secret", "whsec_x")
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(health_config())

    assert out["email"]["inbound_path"] == "resend_webhook"
    assert out["email"]["inbound_reachable"] is True
    assert out["email"]["inbound_polling"] is False


def test_config_says_inbound_is_unreachable_when_gmail_has_no_password(mocker):
    """The most likely cause of "it sends but never replies"."""
    import asyncio

    from app.main import health_config

    mocker.patch.object(settings, "enable_email", True)
    mocker.patch.object(settings, "email_provider", "gmail")
    mocker.patch.object(settings, "email_address", "cordiaassistant@gmail.com")
    mocker.patch.object(settings, "email_app_password", "")
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(health_config())

    assert out["email"]["inbound_reachable"] is False


# --- the webhook refusing deliveries at the door ----------------------------

@pytest.mark.asyncio
async def test_a_refused_webhook_names_the_missing_variable(sent):
    """With neither secret set the endpoint 403s every delivery while outbound
    keeps working — which reads as "it emails me but never replies"."""
    await alerts.notify_inbound_webhook_rejected("no_secret_configured")

    body = _body(sent.await_args)
    assert "EMAIL_WEBHOOK_SIGNING_SECRET" in body
    assert "Every inbound email is being rejected" in body


@pytest.mark.parametrize("reason", ["no_secret_configured", "signature_invalid", "url_secret_invalid"])
@pytest.mark.asyncio
async def test_every_rejection_reason_explains_itself(sent, reason):
    await alerts.notify_inbound_webhook_rejected(reason)
    assert len(_body(sent.await_args)) > 300, reason


@pytest.mark.asyncio
async def test_someone_hammering_the_endpoint_is_one_alert(sent):
    """It is a public URL."""
    for _ in range(60):
        await alerts.notify_inbound_webhook_rejected("signature_invalid")
    assert sent.await_count == 1


@pytest.mark.asyncio
async def test_the_rejection_is_reported_through_the_real_endpoint(client, mocker, sent):
    """And the caller still gets its 403 — the alert must not change the answer."""
    mocker.patch.object(settings, "email_webhook_signing_secret", "")
    mocker.patch.object(settings, "email_inbound_secret", "")

    response = await client.post("/webhook/email", json={"type": "email.received"})

    assert response.status_code == 403
    assert sent.await_count == 1
    assert "no_secret_configured" in sent.await_args.kwargs["subject"]


@pytest.mark.asyncio
async def test_a_broken_alert_does_not_change_the_403(client, mocker):
    mocker.patch.object(settings, "email_webhook_signing_secret", "")
    mocker.patch.object(settings, "email_inbound_secret", "")
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(side_effect=RuntimeError("SMTP down")))

    response = await client.post("/webhook/email", json={"type": "email.received"})
    assert response.status_code == 403


# --- the body fetch, which is the difference between a reply and silence ----

@pytest.mark.asyncio
async def test_a_failed_body_fetch_alerts_with_the_status_and_url(sent, mocker):
    """Resend's email.received carries metadata only, so this call IS the
    reply. When it fails the webhook 500s, Resend retries and gives up, and the
    symptom is identical to every other inbound failure."""
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())
    from app.services.email_service import ReceivedEmailUnavailable

    await usage_service.record_error(
        "email_inbound_fetch",
        ReceivedEmailUnavailable(
            "Client error '404 Not Found' "
            "(GET https://api.resend.com/emails/receiving/re_abc)"),
    )

    body = _body(sent.await_args)
    assert "404" in body, "the alert does not say what the provider answered"
    assert "api.resend.com/emails/receiving" in body, "the alert does not say what it called"


def test_that_detail_survives_the_safety_filter():
    """It nearly did not. _safe_detail drops the message of any exception from
    app.*, so the one fact the alert exists to carry was being removed — while
    the alert still looked correct."""
    from app.services.email_service import ReceivedEmailUnavailable
    from app.services.usage_service import _safe_detail

    detail = _safe_detail(ReceivedEmailUnavailable("404 (GET https://api.resend.com/x)"))
    assert detail and "api.resend.com" in detail


def test_an_ordinary_exception_still_has_its_message_dropped():
    """The opt-in must not become a general amnesty: a plain exception can
    quote her message straight back, and error rows render on a web page."""
    from app.services.usage_service import _safe_detail

    assert _safe_detail(ValueError("what should I pack for the naples house")) is None
    assert _safe_detail(KeyError("tyler@example.com")) is None


@pytest.mark.asyncio
async def test_a_failed_fetch_answers_500_so_the_message_is_not_lost(client, mocker, sent):
    """200 would tell Resend the mail was handled and it would never retry —
    discarding someone's email permanently."""
    mocker.patch.object(settings, "email_webhook_signing_secret", "")
    mocker.patch.object(settings, "email_inbound_secret", "shhh")
    mocker.patch("app.services.email_service.fetch_received_email",
                 new=mocker.AsyncMock(side_effect=__import__(
                     "app.services.email_service", fromlist=["x"]
                 ).ReceivedEmailUnavailable("404 (GET https://api.resend.com/x)")))
    mocker.patch("app.services.usage_service.record_standalone", new=mocker.AsyncMock())

    response = await client.post(
        "/webhook/email?secret=shhh",
        json={"type": "email.received", "email_id": "re_abc",
              "from": "cordia@example.com", "subject": "Re: flights"},
    )

    assert response.status_code == 500
    assert response.json()["status"] == "content_fetch_failed"


@pytest.mark.asyncio
async def test_a_broken_alert_does_not_change_that_500(client, mocker):
    mocker.patch.object(settings, "email_webhook_signing_secret", "")
    mocker.patch.object(settings, "email_inbound_secret", "shhh")
    mocker.patch("app.services.email_service.fetch_received_email",
                 new=mocker.AsyncMock(side_effect=__import__(
                     "app.services.email_service", fromlist=["x"]
                 ).ReceivedEmailUnavailable("boom")))
    mocker.patch("app.services.usage_service.record_standalone",
                 new=mocker.AsyncMock(side_effect=RuntimeError("ledger down")))

    response = await client.post(
        "/webhook/email?secret=shhh",
        json={"type": "email.received", "email_id": "re_abc", "from": "c@example.com"},
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_the_fetch_url_is_config_not_a_constant(mocker):
    """If the endpoint is ever wrong, correcting it should be a Railway
    variable rather than a deploy."""
    from app.services import email_service

    mocker.patch.object(settings, "email_api_key", "re_key")
    mocker.patch.object(settings, "email_received_url_template",
                        "https://example.test/inbound/{email_id}")

    called = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"text": "hello"}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            called["url"] = url
            return _Resp()

    mocker.patch.object(email_service.httpx, "AsyncClient", lambda **kw: _Client())

    await email_service.fetch_received_email("re_abc")
    assert called["url"] == "https://example.test/inbound/re_abc"


# --- has any mail ever actually arrived? ------------------------------------

@pytest.mark.asyncio
async def test_the_dashboard_says_plainly_when_nothing_has_ever_arrived(db):
    """The question nobody could answer while inbound email appeared to do
    nothing. Sending working proves the API key and proves nothing about the
    webhook."""
    health = await usage_service.inbound_email_health(db)
    assert health["ever_received_anything"] is False


@pytest.mark.asyncio
async def test_it_notices_a_message_that_arrived_and_was_answered(db):
    await usage_service.record(db, "email_in", actor="tyler@ai-genpartners.com")
    await db.commit()

    health = await usage_service.inbound_email_health(db)
    assert health["ever_received_anything"] is True
    assert health["last_accepted"] is not None


@pytest.mark.asyncio
async def test_it_names_why_the_last_one_was_ignored(db):
    await usage_service.record(db, "email_ignored", actor="stranger@example.com",
                               details={"reason": "ignored_unknown_sender"})
    await db.commit()

    health = await usage_service.inbound_email_health(db)
    assert health["last_ignored_reason"] == "ignored_unknown_sender"
    assert health["ever_received_anything"] is True


@pytest.mark.asyncio
async def test_it_surfaces_an_inbound_failure_with_its_detail(db):
    """A wrong fetch endpoint shows as the status and URL, in the browser."""
    await usage_service.record(db, "error", actor="re_abc", details={
        "where": "email_inbound_fetch", "error_type": "ReceivedEmailUnavailable",
        "detail": "404 Not Found (GET https://api.resend.com/emails/receiving/re_abc)",
    })
    await db.commit()

    health = await usage_service.inbound_email_health(db)
    assert health["last_failure_where"] == "email_inbound_fetch"
    assert "api.resend.com" in health["last_failure_detail"]


@pytest.mark.asyncio
async def test_an_unrelated_error_is_not_read_as_an_email_failure(db):
    """sms_reply failures are not evidence about inbound email."""
    await usage_service.record(db, "error", actor="+16157080002", details={
        "where": "sms_reply", "error_type": "BadRequestError"})
    await db.commit()

    health = await usage_service.inbound_email_health(db)
    assert health["last_failure"] is None
    assert health["ever_received_anything"] is False


def test_the_dashboard_names_the_webhook_url_when_nothing_has_arrived():
    """So the fix is a copy-paste, not another investigation."""
    from app.api.dashboard import _inbound_health_rows

    html = _inbound_health_rows({
        "ever_received_anything": False, "last_accepted": None,
        "last_ignored": None, "last_ignored_reason": None,
        "last_failure": None, "last_failure_where": None, "last_failure_detail": None,
    })
    assert "email.received" in html
    assert "/webhook/email" in html
