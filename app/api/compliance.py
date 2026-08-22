import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import consent_service
from app.utils.phone import to_e164

logger = logging.getLogger(__name__)
router = APIRouter()

_PRIVACY = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Privacy Policy — Cordia AI</title>
<style>body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p><strong>Cordia AI</strong> is a private SMS-based AI personal-assistant service operated by <strong>AI-Gen Partners</strong> (a service of Marq LLC).<br>
Last updated: June 2026</p>

<h2>Information We Collect</h2>
<p>We collect your mobile phone number and the content of messages you send to the service. We do not collect any other personal information.</p>

<h2>How We Use Your Information</h2>
<p>Your phone number and messages are used solely to provide the Cordia AI assistant service. Message content is processed by Anthropic's Claude AI to generate responses. We do not sell or share your data with third parties for marketing purposes.</p>

<h2>Mobile Information &amp; Text Messaging</h2>
<p>No mobile information will be shared with third parties or affiliates for marketing or promotional purposes. Text messaging originator opt-in data and consent will not be shared with any third parties under any circumstances. Your phone number and message content are used only to operate this service — message delivery through our telecommunications provider and response generation through our AI provider — and for no other purpose.</p>

<h2>Message Storage</h2>
<p>Conversation history is stored securely to provide context for future responses. You may request deletion of your conversation history at any time by contacting us.</p>

<h2>Opt-Out</h2>
<p>Reply <strong>STOP</strong> to any message to unsubscribe immediately. No further messages will be sent after opting out.</p>

<h2>Contact</h2>
<p>AI-Gen Partners (Marq LLC) — <a href="mailto:tyler@aigenpartners.com">tyler@aigenpartners.com</a></p>
</body>
</html>"""

_TERMS = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Terms of Service — Cordia AI</title>
<style>body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}</style>
</head>
<body>
<h1>Terms of Service</h1>
<p><strong>Cordia AI</strong> is operated by <strong>AI-Gen Partners</strong> (a service of Marq LLC).<br>
Last updated: June 2026</p>

<h2>Service Description</h2>
<p>Cordia AI is a private SMS-based AI personal assistant. By texting the service number, you agree to these terms.</p>

<h2>Messaging</h2>
<p>Cordia AI is a personal-assistant text messaging program: subscribers receive conversational replies to their requests and follow-ups they ask for (such as reminders and fare updates). Message frequency varies based on your use of the service. Message and data rates may apply. All messaging costs incurred by the operator are covered by AI-Gen Partners — there is no charge to the end user beyond standard carrier rates.</p>

<h2>Carrier Disclaimer</h2>
<p>Wireless carriers are not liable for delayed or undelivered messages.</p>

<h2>Opt-Out</h2>
<p>Reply <strong>STOP</strong> to unsubscribe at any time. Reply <strong>HELP</strong> for support information.</p>

<h2>Acceptable Use</h2>
<p>This service is for personal assistance purposes only. Do not use the service for unlawful purposes or to send unsolicited messages to others.</p>

<h2>Disclaimer</h2>
<p>This service is provided for informational and personal assistance purposes. For legal, medical, or financial decisions, always consult a qualified professional.</p>

<h2>Contact</h2>
<p><a href="mailto:tyler@aigenpartners.com">tyler@aigenpartners.com</a></p>
</body>
</html>"""

_OPT_IN = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>SMS Program & Consent — Cordia AI</title>
<style>body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}h1{font-size:1.4rem}h2{font-size:1.05rem;margin-top:1.6rem}table{border-collapse:collapse;width:100%}td{border:1px solid #ddd;padding:6px 10px;vertical-align:top}</style>
</head>
<body>
<h1>Cordia AI — SMS Program & Consent Disclosure</h1>
<p><strong>Program name:</strong> Cordia AI<br>
<strong>Program number:</strong> +1 (629) 225-9067<br>
<strong>Operated by:</strong> AI-Gen Partners (a service of Marq LLC)<br>
<strong>Program type:</strong> Private, two-way personal-assistant text service</p>

<h2>About the operator</h2>
<p>AI-Gen Partners is a service of Marq LLC, a Norfolk, Virginia technology company. Marq LLC
builds and operates Cordia AI, a hosted AI personal-assistant software application, as its own
direct offering. All messages in this program are generated and sent by that application on
behalf of Marq LLC — never on behalf of any other business. Contact:
<a href="mailto:tyler@aigenpartners.com">tyler@aigenpartners.com</a>.</p>

<h2>What this program is</h2>
<p>Cordia AI is a private SMS personal assistant operated by AI-Gen Partners and provided
to one authorized individual client and a small number of her specifically authorized
family members. It is a conversational, two-way service: recipients text the assistant
with requests and the assistant replies. It is <strong>not</strong> marketed to or
available to the general public, and no one outside the pre-authorized group can receive
messages.</p>

<h2>Messages you can expect</h2>
<p>Replies to your requests and related follow-ups, including help with travel planning,
scheduling and family-event coordination, reminders, and general personal assistance.
Message frequency varies based on your own activity (conversational).</p>

<h2>Consent is optional — it is not a condition of service</h2>
<p>Opting in to SMS is entirely optional and is <strong>not a condition of any purchase,
service, or transaction</strong>. Authorized individuals who prefer not to receive text
messages can interact with the assistant by email instead — receiving text messages is
never required to use Cordia AI.</p>

<h2>How consent is obtained</h2>
<p>Each authorized recipient who chooses SMS gives prior express written consent through
the <a href="/consent">consent form</a> before any messages are sent. Consent records are
collected and retained by the operator. An authorized recipient then confirms enrollment
by sending an initial text (or replying <strong>START</strong>) to the program number,
<strong>+1 (629) 225-9067</strong>, after which a confirmation message is sent. Consent is never shared with or sold to third
parties, and phone numbers are used solely to operate this assistant.</p>

<h2>Mobile information sharing</h2>
<p>No mobile information will be shared with third parties or affiliates for marketing
or promotional purposes. Text messaging originator opt-in data and consent will not be
shared with any third parties under any circumstances.</p>

<h2>Opt-out & help</h2>
<table>
<tr><td><strong>STOP</strong></td><td>Reply STOP (or OPTOUT, UNSUBSCRIBE, CANCEL, END, QUIT) at any time to unsubscribe. You will receive one confirmation and no further messages.</td></tr>
<tr><td><strong>HELP</strong></td><td>Reply HELP or INFO for support, or email tyler@aigenpartners.com.</td></tr>
<tr><td><strong>Rates</strong></td><td>Message &amp; data rates may apply.</td></tr>
</table>

<p style="margin-top:1.6rem"><a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


_CONSENT_STYLE = """
body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}
h1{font-size:1.4rem}h2{font-size:1.05rem;margin-top:1.6rem}
.form-box{border:2px solid #333;border-radius:6px;padding:24px;margin:1.5rem 0;background:#fafafa}
.form-box h2{margin-top:0;font-size:1.15rem}
.checkbox-row{display:flex;gap:10px;align-items:flex-start;margin:1rem 0}
.checkbox-row input[type=checkbox]{width:18px;height:18px;flex-shrink:0;margin-top:4px}
label.field{display:block;margin-top:1.2rem;font-weight:bold;font-size:.95rem}
input[type=text],input[type=tel]{width:100%;padding:10px;margin-top:4px;border:1px solid #999;border-radius:4px;font-size:1rem;box-sizing:border-box}
button{margin-top:1.6rem;padding:12px 28px;background:#1a6b3c;color:#fff;border:none;border-radius:4px;font-size:1rem;cursor:pointer}
button:hover{background:#155830}
.note{font-size:.9rem;color:#555;font-style:italic}
.success{border:2px solid #1a6b3c;border-radius:6px;padding:24px;background:#f0f9f3}
.error{color:#a4262c;font-weight:bold}
"""

# The Customer Care opt-in form. Shared verbatim between /consent and /opt-in so
# the page reviewers open from the registered Call-to-Action shows the form
# exactly as described in the campaign submission.
_FORM_SECTION = """<div class="form-box">
<h2>Customer Care SMS Consent — Cordia AI by AI-Gen Partners</h2>

<p><strong>Program:</strong> Cordia AI — a private, two-way SMS personal-assistant service<br>
<strong>Program number:</strong> +1 (629) 225-9067<br>
<strong>Operated by:</strong> AI-Gen Partners (Marq LLC)</p>

<form method="post" action="/consent">
<label class="field">Full name
<input type="text" name="full_name" required maxlength="100" placeholder="Your full name">
</label>

<label class="field">Mobile phone number
<input type="tel" name="phone" required maxlength="20" placeholder="(615) 555-1234">
</label>

<div class="checkbox-row">
  <input type="checkbox" name="consent" id="consent" value="yes" required>
  <label for="consent">By providing a telephone number, clicking this button, and submitting
  the form, you are consenting to be contacted by SMS text message from AI-Gen Partners,
  regarding Customer Care messages, (our message frequency may vary). Message &amp; data
  rates apply. Reply <strong>STOP</strong> to unsubscribe from further messaging from
  AI-Gen Partners. Reply <strong>HELP</strong> for more information. Consent is not a
  condition of any purchase, service, or transaction. See our
  <a href="/privacy">Privacy Policy</a> (containing our SMS Terms) at the bottom of the
  page for more information.</label>
</div>

<button type="submit">Submit Consent</button>
</form>
</div>

<p class="note">Consent is provided exclusively for AI-Gen Partners to contact the user
based on the selection, not any other third parties mentioned on the site. SMS opt-in data
is not shared/sold to third parties for promotional/marketing purposes.</p>"""


_CONSENT_FORM = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMS Consent Form — Cordia AI</title>
<style>{_CONSENT_STYLE}</style>
</head>
<body>
<h1>Cordia AI — SMS Program Consent Form</h1>
<p class="note">Cordia AI is a private, invitation-only SMS personal-assistant program
operated by AI-Gen Partners (Marq LLC). This form is how each authorized recipient gives
prior express written consent before being enrolled. Enrollment is limited to
pre-authorized individuals — submitting this form does not grant service access to the
general public.</p>
<p class="note"><strong>Opting in to SMS is optional and is not a condition of any
purchase, service, or transaction.</strong> If you would rather not receive text messages,
you can reach the assistant by email instead — text messaging is never required to use
Cordia AI.</p>

{_FORM_SECTION}

<h2>What happens after you submit</h2>
<p>1. AI-Gen Partners records and retains your consent.<br>
2. Your number is authorized in the Cordia AI system.<br>
3. You confirm enrollment by texting <strong>START</strong> to the program number, <strong>+1 (629) 225-9067</strong>.<br>
4. You receive this confirmation message: <em>"Cordia AI by AI-Gen Partners:
You're subscribed to your personal assistant. Message frequency varies. Msg &amp; data
rates may apply. Reply HELP for help, STOP to unsubscribe."</em></p>

<p style="margin-top:1.6rem"><a href="/opt-in">SMS Program Disclosure</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""

_CONSENT_THANKS = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Consent Recorded — Cordia AI</title>
<style>{_CONSENT_STYLE}</style>
</head>
<body>
<h1>Cordia AI — Consent Recorded</h1>
<div class="success">
<p><strong>Thank you — your consent has been recorded.</strong></p>
<p>One more step: text <strong>START</strong> to the Cordia AI program number,
<strong>+1 (629) 225-9067</strong>, from the mobile phone you listed, and you'll receive a
confirmation message completing your enrollment.</p>
<p class="note">Reply STOP at any time to unsubscribe, or HELP for assistance.
Message &amp; data rates may apply. Questions: tyler@aigenpartners.com</p>
</div>
<p style="margin-top:1.6rem"><a href="/opt-in">SMS Program Disclosure</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


# Shown when a submission still needs Cordia's approval. It confirms the consent
# record honestly (that part IS done) without promising service access, because
# this program is invitation-only and the form is publicly reachable.
_CONSENT_PENDING = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Consent Recorded — Cordia AI</title>
<style>{_CONSENT_STYLE}</style>
</head>
<body>
<h1>Cordia AI — Consent Recorded</h1>
<div class="success">
<p><strong>Thank you — your consent has been recorded.</strong></p>
<p>Cordia AI is a private, invitation-only assistant, so one more step happens on
our side: the account holder confirms each new recipient before enrollment is
completed. You&rsquo;ll receive a confirmation text at the number you provided once
that&rsquo;s done. If you were not expecting this, no further action is needed and you
will not be messaged.</p>
<p class="note">Reply STOP at any time to unsubscribe, or HELP for assistance.
Message &amp; data rates may apply. Questions: tyler@aigenpartners.com</p>
</div>
<p style="margin-top:1.6rem"><a href="/opt-in">SMS Program Disclosure</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


# Embed the consent form (and its styles) on the opt-in disclosure page, right
# below the program header — the registered CTA says users who open this page
# "see a form to fill out," so the form must be here verbatim.
_OPT_IN = _OPT_IN.replace("</style>", _CONSENT_STYLE + "</style>")
_OPT_IN = _OPT_IN.replace(
    "<h2>What this program is</h2>",
    "<h2>Opt in to Cordia AI text messages</h2>\n" + _FORM_SECTION + "\n\n<h2>What this program is</h2>",
)


@router.get("/opt-in", include_in_schema=False)
async def opt_in_page():
    return HTMLResponse(_OPT_IN)


@router.get("/consent", include_in_schema=False)
async def consent_form_page():
    return HTMLResponse(_CONSENT_FORM)


@router.post("/consent", include_in_schema=False)
async def consent_form_submit(
    full_name: str = Form(...),
    phone: str = Form(...),
    consent: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    digits = re.sub(r"\D", "", phone)
    if consent != "yes" or not full_name.strip() or len(digits) < 10:
        return HTMLResponse(_CONSENT_FORM.replace(
            "<h1>Cordia AI — SMS Program Consent Form</h1>",
            '<h1>Cordia AI — SMS Program Consent Form</h1>'
            '<p class="error">Please provide your name, a valid mobile number, '
            "and check the consent box.</p>",
        ), status_code=400)

    # The 10DLC campaign is US-only, so the form's own numbers are US numbers -
    # but the formatter is shared so a number that arrives already carrying a
    # country code is not rewritten into a different one.
    normalized = to_e164(phone) or f"+1{digits[-10:]}"
    now = datetime.now(timezone.utc)
    # The table is created by migration 015, not lazily here. Creating it on
    # first submission meant every database where nobody had submitted the form
    # yet was missing a table that list_consent_requests joins unguarded.
    await db.execute(
        text("INSERT INTO consent_submissions (full_name, phone, submitted_at) "
             "VALUES (:name, :phone, :ts)"),
        {"name": full_name.strip()[:100], "phone": normalized, "ts": now},
    )
    # The consent record is written unconditionally — it is the legal evidence
    # that this person opted in, and it is never withheld or edited. What the
    # form does NOT do is grant access: the page is publicly linked, so a new
    # submission lands as 'pending' until Cordia approves it. Someone already
    # approved who re-signs keeps their approval rather than being demoted.
    result = await db.execute(
        text("INSERT INTO sms_consent (phone, consented_at, method, approval_status) "
             "VALUES (:phone, :ts, 'web_form', 'pending') "
             "ON CONFLICT (phone) DO UPDATE SET consented_at = EXCLUDED.consented_at, "
             "method = 'web_form', opted_out_at = NULL, "
             "approval_status = CASE WHEN sms_consent.approval_status = 'approved' "
             "THEN 'approved' ELSE 'pending' END "
             "RETURNING approval_status"),
        {"phone": normalized, "ts": now},
    )
    approval_status = (result.scalar() or "pending")
    await db.commit()
    logger.info(f"Consent form submitted for {normalized} (approval={approval_status})")

    if approval_status != "approved":
        # Ask Cordia to make the call. Never let a notification failure break
        # someone's consent submission — the record is already safely written.
        await consent_service.notify_owner_of_submission(full_name.strip(), normalized)
        return HTMLResponse(_CONSENT_PENDING)
    return HTMLResponse(_CONSENT_THANKS)


@router.get("/privacy", include_in_schema=False)
async def privacy_page():
    return HTMLResponse(_PRIVACY)


@router.get("/terms", include_in_schema=False)
async def terms_page():
    return HTMLResponse(_TERMS)
