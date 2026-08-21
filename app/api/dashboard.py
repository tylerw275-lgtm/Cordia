"""A human-readable status page.

The JSON health endpoints are fine for scripts and terrible to read in a
browser. This renders the same information as a page you can scan in a few
seconds: what is working, what is not, and what to do about it.

Phone numbers appear in full here, deliberately. This page is admin-gated and
exists so the operator can audit the consent record — a masked number cannot
be traced back to a person, which defeats the purpose. The masking that matters
is in the *tool results*, which reach the model: Cord still never sees a stored
number, so it cannot repeat one into a text or an email.
"""
import base64
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_STYLE = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;
background:#f6f7f9;color:#1a1d21;line-height:1.5}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}
.sub{color:#6b7280;font-size:.9rem;margin-bottom:24px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px;margin-bottom:16px}
.card h2{font-size:1.05rem;margin:0 0 14px;display:flex;align-items:center;gap:8px}
.row{display:flex;justify-content:space-between;align-items:center;padding:9px 0;
border-bottom:1px solid #f0f1f3;gap:12px;flex-wrap:wrap}
.row:last-child{border-bottom:none}
.label{color:#4b5563}
.decide{display:flex;gap:6px;margin:0}
.decide button{padding:5px 12px;border:none;border-radius:5px;font-size:.82rem;
font-weight:600;cursor:pointer;color:#fff}
.btn-ok{background:#1a6b3c}.btn-ok:hover{background:#155830}
.btn-no{background:#a4262c}.btn-no:hover{background:#871f24}
.val{font-weight:600;text-align:right}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.8rem;font-weight:600}
.ok{background:#e7f6ec;color:#11683a}
.bad{background:#fdeaea;color:#a4262c}
.warn{background:#fff4e0;color:#8a5a00}
.off{background:#eef0f2;color:#5b6470}
table{width:100%;border-collapse:collapse;font-size:.92rem}
th{text-align:left;color:#6b7280;font-weight:600;font-size:.8rem;text-transform:uppercase;
letter-spacing:.03em;padding:6px 8px;border-bottom:2px solid #e5e7eb}
td{padding:9px 8px;border-bottom:1px solid #f0f1f3}
tr:last-child td{border-bottom:none}
.note{background:#fff9e6;border-left:3px solid #f0b429;padding:10px 12px;margin-top:12px;
border-radius:0 6px 6px 0;font-size:.9rem}
.empty{color:#6b7280;font-style:italic;padding:8px 0}
.actions a{display:inline-block;margin:4px 8px 4px 0;padding:8px 14px;background:#1a6b3c;
color:#fff;text-decoration:none;border-radius:6px;font-size:.9rem}
.actions a.secondary{background:#eef0f2;color:#1a1d21}
code{background:#f0f1f3;padding:1px 5px;border-radius:4px;font-size:.85em}
"""


_COOKIE = "cord_admin"


def _signing_key() -> bytes:
    """Derived from the password itself, so changing the password invalidates
    every existing session without needing a second secret."""
    return hashlib.sha256(
        (settings.dashboard_password + "|cord-dashboard-session").encode()
    ).digest()


def _issue_session() -> str:
    expires = str(int(time.time()) + settings.dashboard_session_hours * 3600)
    sig = hmac.new(_signing_key(), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def _session_valid(token: str | None) -> bool:
    if not token or not settings.dashboard_password:
        return False
    expires, _, sig = token.partition(".")
    if not expires.isdigit() or not sig:
        return False
    expected = hmac.new(_signing_key(), expires.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return int(expires) > time.time()


def _authorized(request: Request) -> bool:
    """A valid session cookie, or the admin secret for scripted access."""
    if _session_valid(request.cookies.get(_COOKIE)):
        return True
    supplied = request.query_params.get("secret") or request.headers.get("X-Admin-Secret") or ""
    return bool(settings.admin_api_secret) and hmac.compare_digest(
        supplied.encode(), settings.admin_api_secret.encode()
    )


_LOGIN_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cord — Sign in</title><style>{style}
.login{{max-width:360px;margin:12vh auto;padding:0 16px}}
.login .card{{padding:28px}}
input[type=password]{{width:100%;padding:11px;border:1px solid #cbd0d6;border-radius:6px;
font-size:1rem;margin-top:6px}}
button{{width:100%;margin-top:16px;padding:11px;background:#1a6b3c;color:#fff;border:none;
border-radius:6px;font-size:1rem;font-weight:600;cursor:pointer}}
button:hover{{background:#155830}}
.err{{background:#fdeaea;color:#a4262c;padding:10px 12px;border-radius:6px;
font-size:.9rem;margin-bottom:14px}}
</style></head><body><div class="login"><div class="card">
<h1 style="font-size:1.25rem;margin:0 0 6px">Cord</h1>
<div class="sub" style="margin-bottom:20px">Status dashboard</div>
{error}
<form method="post" action="/health/login">
<label style="font-size:.9rem;color:#4b5563">Password
<input type="password" name="password" autofocus required autocomplete="current-password">
</label>
<button type="submit">Sign in</button>
</form>
</div></div></body></html>"""


@router.get("/login", include_in_schema=False)
async def login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_PAGE.format(style=_STYLE, error=""))


@router.post("/login", include_in_schema=False)
async def login(password: str = Form(...)):
    if not settings.dashboard_password:
        logger.error("Dashboard login attempted but DASHBOARD_PASSWORD is not set")
        return HTMLResponse(_LOGIN_PAGE.format(
            style=_STYLE,
            error='<div class="err">No dashboard password is configured on the server.</div>',
        ), status_code=503)

    if not hmac.compare_digest(password.encode(), settings.dashboard_password.encode()):
        logger.warning("Failed dashboard login attempt")
        # Slow brute force without holding the worker for long.
        import asyncio
        await asyncio.sleep(1.0)
        return HTMLResponse(_LOGIN_PAGE.format(
            style=_STYLE, error='<div class="err">Incorrect password.</div>',
        ), status_code=401)

    response = RedirectResponse(url="/health/dashboard", status_code=303)
    response.set_cookie(
        _COOKIE, _issue_session(),
        max_age=settings.dashboard_session_hours * 3600,
        httponly=True, secure=True, samesite="strict", path="/health",
    )
    return response


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse(url="/health/login", status_code=303)
    response.delete_cookie(_COOKIE, path="/health")
    return response


def _format_phone(raw: str) -> str:
    """(615) 853-9483 — readable, and dialable straight from a phone."""
    d = "".join(c for c in (raw or "") if c.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10:
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    return raw or "—"


@router.post("/consent-decision", include_in_schema=False)
async def consent_decision(request: Request, phone: str = Form(...), decision: str = Form(...)):
    """Approve or reject one pending number, from the dashboard.

    Session-gated like the rest of the page. Rejecting only sets a flag — the
    consent record itself is compliance evidence and is never touched.
    """
    if not _authorized(request):
        return HTMLResponse(_LOGIN_PAGE.format(style=_STYLE, error=""), status_code=401)
    if decision not in ("approved", "rejected"):
        return RedirectResponse(url="/health/dashboard", status_code=303)

    from app.database import get_db_session
    from app.services import consent_service
    try:
        async with get_db_session() as db:
            await consent_service.set_status(db, phone, decision)
    except Exception as e:
        logger.error(f"Consent decision failed for {phone}: {e}")
    return RedirectResponse(url="/health/dashboard", status_code=303)


def _decide_buttons(phone: str) -> str:
    return (
        '<form method="post" action="/health/consent-decision" class="decide">'
        f'<input type="hidden" name="phone" value="{phone}">'
        '<button name="decision" value="approved" class="btn-ok">Approve</button>'
        '<button name="decision" value="rejected" class="btn-no">Reject</button>'
        "</form>"
    )


def _local(dt, fmt: str = "%b %-d, %Y at %-I:%M %p") -> str:
    """Render a stored UTC timestamp in Cordia's timezone.

    Everything is stored in UTC. Showing it raw made a 7:09 PM submission read
    as "Aug 21, 00:09" and look like it came from someone else in the night.
    """
    if not dt:
        return "—"
    from zoneinfo import ZoneInfo
    from datetime import timezone as _tz
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    try:
        local = dt.astimezone(ZoneInfo(settings.scheduler_timezone))
    except Exception:
        local = dt
    return local.strftime(fmt)


def _pill(ok: bool, yes: str = "Working", no: str = "Not set") -> str:
    return f'<span class="pill {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def _row(label: str, value: str) -> str:
    return f'<div class="row"><span class="label">{label}</span><span class="val">{value}</span></div>'


@router.get("/dashboard", include_in_schema=False)
async def dashboard(request: Request, secret: str = "") -> HTMLResponse:
    if not _authorized(request):
        return HTMLResponse(_LOGIN_PAGE.format(style=_STYLE, error=""), status_code=401)
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.database import get_db_session
    from app.models.contact import Contact
    from app.models.family import FamilyMember
    from app.utils.phone import normalize_phone
    from sqlalchemy import text as sa_text

    # ---- consent picture -------------------------------------------------
    consent_rows, people, circle_access, submitted_names = [], {}, {}, {}
    db_error = None
    try:
        async with get_db_session() as db:
            consent_rows = (await db.execute(sa_text(
                "SELECT phone, method, consented_at, opted_out_at, approval_status "
                "FROM sms_consent "
                "ORDER BY consented_at DESC"
            ))).fetchall()
            # The name typed on the form — the only clue to who an
            # unrecognised pending number belongs to.
            try:
                for r in (await db.execute(sa_text(
                    "SELECT full_name, phone FROM consent_submissions "
                    "ORDER BY submitted_at DESC"
                ))).fetchall():
                    submitted_names.setdefault(normalize_phone(r.phone), r.full_name)
            except Exception:
                # The table only exists once someone has used the form. Roll
                # back explicitly: a failed statement poisons the transaction,
                # and every later query — the whole roster — would fail too.
                await db.rollback()
            for m in (await db.execute(
                select(FamilyMember).where(FamilyMember.phone.isnot(None))
            )).scalars():
                if (k := normalize_phone(m.phone)):
                    people[k] = m.name
                    circle_access[k] = bool(m.has_circle_access)
            for c in (await db.execute(
                select(Contact).where(Contact.phone.isnot(None))
            )).scalars():
                if (k := normalize_phone(c.phone)):
                    people.setdefault(k, c.name)
    except Exception as e:
        db_error = str(e)
        logger.error(f"Dashboard could not read the database: {e}")

    can_text, awaiting, rejected, opted_out, unmatched = [], [], [], [], []
    matched_keys = set()
    for phone, method, consented_at, opted_out_at, approval_status in consent_rows:
        key = normalize_phone(phone)
        matched_keys.add(key)
        name = people.get(key)
        when = _local(consented_at)
        how = {"web_form": "Signed the form", "keyword_start": "Texted START",
               "inbound_text": "Texted in"}.get(method, method or "—")
        can_reply = (
            '<span class="pill ok">yes</span>' if circle_access.get(key)
            else '<span class="pill warn">needs access</span>'
        )
        entry = (name or f"<em>{submitted_names.get(key) or 'unknown'}</em>",
                 _format_phone(phone), how, when, can_reply)
        status = approval_status or "pending"
        if opted_out_at:
            opted_out.append(entry)
        elif status == "rejected":
            rejected.append(entry[:4] + (_decide_buttons(phone).replace(
                '<button name="decision" value="rejected" class="btn-no">Reject</button>', ''),))
        elif status != "approved":
            awaiting.append(entry[:4] + (_decide_buttons(phone),))
        elif name:
            can_text.append(entry)
        else:
            unmatched.append(entry)
    no_consent = sorted(n for k, n in people.items() if k not in matched_keys)

    # ---- what it costs ---------------------------------------------------
    usage_month = usage_all = credit = None
    try:
        from datetime import timedelta

        from app.services import usage_service
        async with get_db_session() as db:
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            usage_month = await usage_service.summary(db, since=month_start)
            usage_all = await usage_service.summary(db)
            credit = await usage_service.credit_status(db)
    except Exception as e:
        logger.error(f"Dashboard could not read usage: {e}")

    # ---- channel health --------------------------------------------------
    sms_ready = bool(
        settings.signalhouse_api_key and settings.signalhouse_phone_number
    ) if settings.sms_provider == "signalhouse" else bool(settings.twilio_auth_token)
    email_ready = settings.enable_email and (
        bool(settings.email_api_key) if settings.email_provider == "resend"
        else bool(settings.email_address and settings.email_app_password)
    )
    inbound_email_ready = settings.enable_email and (
        bool(settings.email_webhook_signing_secret or settings.email_inbound_secret)
        if settings.email_provider == "resend"
        else bool(settings.email_address and settings.email_app_password)
    )

    def _table(rows, headers, empty_msg):
        if not rows:
            return f'<div class="empty">{empty_msg}</div>'
        head = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def _money(v) -> str:
        v = float(v or 0)
        # Sub-cent totals are normal early on; showing "$0.00" would read as
        # "nothing is being tracked" when in fact it is working.
        return f"${v:,.2f}" if v >= 0.01 else f"${v:.4f}"

    _USAGE_LABELS = [
        ("sms_out", "Texts sent", "segments"),
        ("sms_in", "Texts received", "segments"),
        ("mms_out", "Photos sent", "messages"),
        ("mms_in", "Photos received", "messages"),
        ("email_out", "Emails sent", "emails"),
        ("email_in", "Emails received", "emails"),
        ("ai_turn", "AI requests", "requests"),
        ("web_search", "Web searches", "searches"),
        ("web_fetch", "Pages read", "pages"),
    ]

    def _usage_card(month, all_time, credit) -> str:
        if month is None:
            return ('<div class="card"><h2>What it costs</h2>'
                    '<div class="note">Could not read the usage ledger.</div></div>')
        rows = []
        for key, label, unit in _USAGE_LABELS:
            m = month["by_type"].get(key, {"quantity": 0, "cost": 0.0})
            a = all_time["by_type"].get(key, {"quantity": 0, "cost": 0.0})
            if not m["quantity"] and not a["quantity"]:
                continue
            rows.append((label, f'{m["quantity"]:,} {unit}', _money(m["cost"]),
                         f'{a["quantity"]:,}', _money(a["cost"])))
        table = _table(
            rows, ["", "This month", "Cost", "All time", "Cost"],
            "Nothing recorded yet — this fills in as Cord is used.",
        )
        people = _table(
            [(_format_phone(p["actor"]) if any(c.isdigit() for c in (p["actor"] or "")) and "@" not in (p["actor"] or "")
              else p["actor"], f'{p["events"]:,}', _money(p["cost"]))
             for p in month["by_actor"][:10]],
            ["Person", "Events", "Cost this month"],
            "No activity yet this month.",
        )
        tokens = (f'{month["input_tokens"]:,} in / {month["output_tokens"]:,} out'
                  if month["input_tokens"] or month["output_tokens"] else "—")
        if credit and credit["purchased"]:
            left = credit["remaining"]
            # Colour the number, because this is the one figure on the page that
            # eventually requires an action.
            colour = "#a4262c" if left < 10 else ("#8a6d0b" if left < 25 else "#1a6b3c")
            if credit["days_remaining"] is not None:
                runway = (f' &mdash; about {credit["days_remaining"]:,} days left at '
                          f'{_money(credit["daily_burn"])}/day')
            else:
                runway = (' &mdash; not enough history yet to estimate how long '
                          'that lasts')
            credit_row = (
                f'<div class="row"><span class="label">Signal House credit remaining'
                f'{runway}</span>'
                f'<span class="val" style="color:{colour}">{_money(left)}</span></div>'
                f'<div class="row"><span class="label">&nbsp;&nbsp;of the '
                f'{_money(credit["purchased"])} bought</span>'
                f'<span class="val">{_money(credit["spent"])} used</span></div>'
            )
        else:
            credit_row = ""

        setup = float(settings.setup_cost_to_date or 0)
        setup_row = (
            f'<div class="row"><span class="label">Spent getting set up '
            f'<span style="color:#6b7280">(one-off, not in the totals above)</span></span>'
            f'<span class="val">{_money(setup)}</span></div>' if setup else ""
        )
        return f"""<div class="card">
<h2>What it costs</h2>
<div class="row"><span class="label">This month so far</span>
<span class="val" style="font-size:1.35rem">{_money(month["total_cost"])}</span></div>
<div class="row"><span class="label">&nbsp;&nbsp;of that, messages &amp; AI</span>
<span class="val">{_money(month["usage_cost"])}</span></div>
<div class="row"><span class="label">&nbsp;&nbsp;of that, number renewal
<span style="color:#6b7280">(charged monthly either way)</span></span>
<span class="val">{_money(month["fixed_cost"])}</span></div>
<div class="row"><span class="label">Messages &amp; AI, all time</span>
<span class="val">{_money(all_time["total_cost"])}</span></div>
{setup_row}
{credit_row}
<div class="row"><span class="label">AI tokens this month</span>
<span class="val">{tokens}</span></div>
{table}
<h2 style="margin-top:22px">By person, this month</h2>
{people}
<div class="note"><strong>How these are worked out.</strong> Texts bill per
<em>segment</em>, not per message — a long reply is several segments, and one
emoji drops the limit from 160 characters to 70. Signal House charges a platform
fee plus a carrier passthrough, and the two directions differ: sending is
{_money(settings.sms_cost_outbound)} a segment, receiving
{_money(settings.sms_cost_inbound)}. A <strong>photo is an MMS</strong>, priced
per message rather than per segment, at {_money(settings.mms_cost_outbound)} —
about seven times a text. AI cost uses Anthropic's list prices for
{settings.claude_model}, with cached input at a tenth of the normal rate, and web
search is {_money(settings.web_search_cost)} per search. The number renews at
{_money(settings.monthly_number_cost)} a month whether or not anyone sends
anything; the 10DLC campaign and brand fees were one-time and sit in setup.
Texting rates are reconciled against the first invoice and assume this account's
carrier mix (T-Mobile and Verizon), so a message to a different network can
differ by a few hundredths of a cent. Rates live in Railway (<code>SMS_COST_OUTBOUND</code>,
<code>MMS_COST_OUTBOUND</code>, <code>MONTHLY_CAMPAIGN_COST</code>,
<code>SETUP_COST_TO_DATE</code>, and so on). Each charge is stored at the rate in
force when it happened, so changing a rate never rewrites past months, and
nothing before this ledger existed is counted.</div>
</div>"""

    usage_section = _usage_card(usage_month, usage_all, credit)

    awaiting_section = "" if not awaiting else (
        '<div class="card">'
        '<h2>&#9888; Waiting for your approval</h2>'
        '<div class="note">The consent form is a public link, so anyone can sign it. '
        'Signing does <strong>not</strong> let them reach Cord &mdash; these people are '
        'on hold until you decide. Text Cord <em>"approve"</em> or <em>"reject"</em> with '
        'the last four digits &mdash; or decide right here.</div>'
        + _table(awaiting, ["Name they gave", "Number", "How", "Signed", "Decision"], "")
        + "</div>"
    )

    q = f"?secret={secret}" if secret else ""
    now = _local(datetime.now(timezone.utc)) + f" ({settings.scheduler_timezone.split('/')[-1].replace('_', ' ')})"

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cord — Status</title><style>{_STYLE}</style></head><body><div class="wrap">
<h1>Cord — Status</h1>
<div class="sub">Checked {now}</div>

{awaiting_section}

<div class="card">
<h2>Who Cord can text</h2>
{_table(can_text, ["Name", "Number", "How they consented", "Date", "Replies reach Cord"],
        "Nobody has consented yet.")}
<div class="note"><strong>Replies reach Cord?</strong> Consent lets Cord text them.
Circle access lets their <em>answer</em> come back. Anyone marked
<span class="pill warn">needs access</span> can be texted, but Cord would not
see what they send back — ask Cord to give them circle access.</div>
{f'<div class="note"><strong>Consented, but not matched to anyone.</strong> '
 f'Someone signed the consent form with a number that is not on any profile. '
 f'Call or text it to find out who it is, then either add that number to their '
 f'profile (so Cord can text them) or ignore it if it was a test.'
 f'{_table(unmatched, ["Name", "Number", "How", "Date", ""], "")}</div>' if unmatched else ''}
{f'<div class="note"><strong>Rejected.</strong> You blocked these numbers. Their '
 f'consent record is kept as required proof, but they can never reach Cord.'
 f'{_table(rejected, ["Name they gave", "Number", "How", "Signed", ""], "")}</div>' if rejected else ''}
{f'<div class="note"><strong>Opted out.</strong> Cord will never text these numbers.'
 f'{_table(opted_out, ["Name", "Number", "How", "Date", ""], "")}</div>' if opted_out else ''}
</div>

<div class="card">
<h2>Has a number on file, but has not consented</h2>
{'<div class="empty">Everyone with a number on file has consented.</div>' if not no_consent
 else '<div class="empty">Cord cannot text these people yet. Send them the consent '
      'form link from your own phone.</div><table><tbody>'
      + "".join(f"<tr><td>{n}</td></tr>" for n in no_consent) + "</tbody></table>"}
</div>

<div class="card">
<h2>Text messaging</h2>
{_row("Status", _pill(sms_ready))}
{_row("Provider", settings.sms_provider.replace("signalhouse", "Signal House").title())}
{_row("Cord's number", settings.signalhouse_phone_number or settings.twilio_phone_number or "—")}
{_row("Cordia's phone", "on file" if settings.cordia_phone_number else
      '<span class="pill bad">missing</span>')}
</div>

<div class="card">
<h2>Email</h2>
{_row("Sending", _pill(email_ready))}
{_row("Receiving", _pill(inbound_email_ready))}
{_row("Provider", settings.email_provider.title())}
{_row("Cord sends from", settings.email_from or
      (f"{settings.email_from_name} &lt;{settings.email_address}&gt;" if settings.email_address else "—"))}
{_row("Cordia's inbox", settings.owner_email or '<span class="pill bad">missing</span>')}
{_row("Naples house inbox", '<span class="pill ok">on</span>' if
      (settings.naples_email_address and settings.naples_email_app_password)
      else '<span class="pill off">not set up</span>')}
</div>

{usage_section}

<div class="card">
<h2>Features</h2>
{_row("Flight search &amp; fare tracking",
      _pill(settings.enable_flight_search and bool(settings.duffel_access_token)))}
{_row("Flight booking", '<span class="pill ok">on</span>' if settings.enable_flight_booking
      else '<span class="pill off">off</span>')}
{_row("Lease review", '<span class="pill ok">on</span>' if settings.enable_lease_review
      else '<span class="pill off">off</span>')}
{_row("Messaging people for her", '<span class="pill ok">on</span>' if settings.enable_outbound
      else '<span class="pill off">off</span>')}
{_row("Storing loyalty numbers",
      _pill(bool(settings.loyalty_encryption_key), "Working", "Needs encryption key"))}
</div>

{f'<div class="card"><h2>Database</h2><div class="note">Could not read the database: '
 f'<code>{db_error[:200]}</code></div></div>' if db_error else ''}

<div class="card">
<h2>Run a test</h2>
<div class="actions">
<a href="/health/test-email{q}">Send a test email</a>
<a href="/health/test-flights{q}">Search live flights</a>
<a class="secondary" href="/health/config{q}">Raw configuration</a>
<a class="secondary" href="/health/logout">Sign out</a>
</div>
</div>

</div></body></html>"""
    return HTMLResponse(html)
