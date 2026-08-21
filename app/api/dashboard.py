"""A human-readable status page.

The JSON health endpoints are fine for scripts and terrible to read in a
browser. This renders the same information as a page you can scan in a few
seconds: what is working, what is not, and what to do about it.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.api.deps import require_admin
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


def _pill(ok: bool, yes: str = "Working", no: str = "Not set") -> str:
    return f'<span class="pill {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def _row(label: str, value: str) -> str:
    return f'<div class="row"><span class="label">{label}</span><span class="val">{value}</span></div>'


@router.get("/dashboard", include_in_schema=False, dependencies=[Depends(require_admin)])
async def dashboard(secret: str = "") -> HTMLResponse:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.database import get_db_session
    from app.models.contact import Contact
    from app.models.family import FamilyMember
    from app.utils.phone import normalize_phone
    from sqlalchemy import text as sa_text

    # ---- consent picture -------------------------------------------------
    consent_rows, people = [], {}
    db_error = None
    try:
        async with get_db_session() as db:
            consent_rows = (await db.execute(sa_text(
                "SELECT phone, method, consented_at, opted_out_at FROM sms_consent "
                "ORDER BY consented_at DESC"
            ))).fetchall()
            for m in (await db.execute(
                select(FamilyMember).where(FamilyMember.phone.isnot(None))
            )).scalars():
                if (k := normalize_phone(m.phone)):
                    people[k] = m.name
            for c in (await db.execute(
                select(Contact).where(Contact.phone.isnot(None))
            )).scalars():
                if (k := normalize_phone(c.phone)):
                    people.setdefault(k, c.name)
    except Exception as e:
        db_error = str(e)
        logger.error(f"Dashboard could not read the database: {e}")

    can_text, pending, opted_out, unmatched = [], [], [], []
    matched_keys = set()
    for phone, method, consented_at, opted_out_at in consent_rows:
        key = normalize_phone(phone)
        matched_keys.add(key)
        name = people.get(key)
        when = consented_at.strftime("%b %-d, %Y") if consented_at else "—"
        how = {"web_form": "Signed the form", "keyword_start": "Texted START",
               "inbound_text": "Texted in"}.get(method, method or "—")
        entry = (name, f"...{normalize_phone(phone)[-4:]}", how, when)
        if opted_out_at:
            opted_out.append(entry)
        elif name:
            can_text.append(entry)
        else:
            unmatched.append(entry)
    no_consent = sorted(n for k, n in people.items() if k not in matched_keys)

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

    q = f"?secret={secret}" if secret else ""
    now = datetime.now(timezone.utc).strftime("%b %-d, %Y at %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cord — Status</title><style>{_STYLE}</style></head><body><div class="wrap">
<h1>Cord — Status</h1>
<div class="sub">Checked {now}</div>

<div class="card">
<h2>Who Cord can text</h2>
{_table(can_text, ["Name", "Number", "How they consented", "Date"],
        "Nobody has consented yet.")}
{f'<div class="note"><strong>Consented, but not matched to anyone.</strong> '
 f'These numbers signed the form, but no one on file has that number — '
 f'likely they signed with a different phone than the one on their profile. '
 f'Update the profile number to match and they will appear above.'
 f'{_table(unmatched, ["Name", "Number", "How", "Date"], "")}</div>' if unmatched else ''}
{f'<div class="note"><strong>Opted out.</strong> Cord will never text these numbers.'
 f'{_table(opted_out, ["Name", "Number", "How", "Date"], "")}</div>' if opted_out else ''}
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
</div>
</div>

</div></body></html>"""
    return HTMLResponse(html)
