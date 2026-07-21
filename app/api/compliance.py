from fastapi import APIRouter
from fastapi.responses import HTMLResponse

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
<p>AI-Gen Partners (Marq LLC) — <a href="mailto:tyler@ai-genpartners.com">tyler@ai-genpartners.com</a></p>
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
<p>Message and data rates may apply. All messaging costs incurred by the operator are covered by AI-Gen Partners — there is no charge to the end user beyond standard carrier rates.</p>

<h2>Opt-Out</h2>
<p>Reply <strong>STOP</strong> to unsubscribe at any time. Reply <strong>HELP</strong> for support information.</p>

<h2>Acceptable Use</h2>
<p>This service is for personal assistance purposes only. Do not use the service for unlawful purposes or to send unsolicited messages to others.</p>

<h2>Disclaimer</h2>
<p>This service is provided for informational and personal assistance purposes. For legal, medical, or financial decisions, always consult a qualified professional.</p>

<h2>Contact</h2>
<p><a href="mailto:tyler@ai-genpartners.com">tyler@ai-genpartners.com</a></p>
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
<strong>Operated by:</strong> AI-Gen Partners (a service of Marq LLC)<br>
<strong>Program type:</strong> Private, two-way personal-assistant text service</p>

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

<h2>How consent is obtained</h2>
<p>Each authorized recipient gives prior express consent directly to AI-Gen Partners
before being added to the program. Consent records are collected and retained by the
operator. An authorized recipient then confirms enrollment by sending an initial text
(or replying <strong>START</strong>) to the program number, after which a confirmation
message is sent. Consent is never shared with or sold to third parties, and phone numbers
are used solely to operate this assistant.</p>

<h2>Mobile information sharing</h2>
<p>No mobile information will be shared with third parties or affiliates for marketing
or promotional purposes. Text messaging originator opt-in data and consent will not be
shared with any third parties under any circumstances.</p>

<h2>Opt-out & help</h2>
<table>
<tr><td><strong>STOP</strong></td><td>Reply STOP (or OPTOUT, UNSUBSCRIBE, CANCEL, END, QUIT) at any time to unsubscribe. You will receive one confirmation and no further messages.</td></tr>
<tr><td><strong>HELP</strong></td><td>Reply HELP or INFO for support, or email tyler@ai-genpartners.com.</td></tr>
<tr><td><strong>Rates</strong></td><td>Message &amp; data rates may apply.</td></tr>
</table>

<p style="margin-top:1.6rem"><a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


_CONSENT_FORM = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>SMS Consent Form — Cordia AI</title>
<style>
body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}
h1{font-size:1.4rem}h2{font-size:1.05rem;margin-top:1.6rem}
.form-box{border:2px solid #333;border-radius:6px;padding:24px;margin:1.5rem 0;background:#fafafa}
.form-box h2{margin-top:0;font-size:1.15rem}
.checkbox-row{display:flex;gap:10px;align-items:flex-start;margin:1rem 0}
.checkbox{width:18px;height:18px;border:2px solid #333;border-radius:3px;flex-shrink:0;margin-top:3px;background:#fff}
.sig-line{border-bottom:1px solid #333;height:1.6rem;margin-top:1.4rem}
.sig-label{font-size:.85rem;color:#555}
.field-line{border-bottom:1px solid #333;height:1.4rem;margin-top:1rem}
.note{font-size:.9rem;color:#555;font-style:italic}
</style>
</head>
<body>
<h1>Cordia AI — SMS Program Written Consent Form</h1>
<p class="note">This page hosts the written consent form that each authorized recipient
completes before being enrolled in the Cordia AI SMS program. It is published here so the
consent experience is publicly verifiable. The program is private and invitation-only —
this form is provided by AI-Gen Partners directly to each authorized recipient; there is
no public sign-up.</p>

<div class="form-box">
<h2>SMS Messaging Consent — Cordia AI by AI-Gen Partners</h2>

<p><strong>Program:</strong> Cordia AI — a private, two-way SMS personal-assistant service<br>
<strong>Operated by:</strong> AI-Gen Partners (Marq LLC)</p>

<div class="checkbox-row">
  <div class="checkbox"></div>
  <div>I authorize AI-Gen Partners to send me recurring SMS text messages from Cordia AI,
  my personal assistant, at the mobile number I provide below. I understand that message
  frequency varies based on my own use of the service, that message and data rates may
  apply, that I can reply <strong>STOP</strong> at any time to unsubscribe and
  <strong>HELP</strong> for assistance, and that consent is not a condition of any purchase.
  I have reviewed the <a href="/privacy">Privacy Policy</a> and
  <a href="/terms">Terms of Service</a>. My mobile number and opt-in information will not
  be shared with third parties.</div>
</div>

<p class="note">Checkbox is unchecked by default — the recipient must actively check it
to give consent.</p>

<div class="field-line"></div>
<p class="sig-label">Full name</p>

<div class="field-line"></div>
<p class="sig-label">Mobile phone number</p>

<div class="sig-line"></div>
<p class="sig-label">Signature</p>

<div class="sig-line"></div>
<p class="sig-label">Date</p>
</div>

<h2>What happens after this form is completed</h2>
<p>1. AI-Gen Partners retains the signed consent record.<br>
2. The recipient's number is authorized in the Cordia AI system.<br>
3. The recipient texts <strong>START</strong> to the program number to confirm enrollment.<br>
4. The recipient receives this confirmation message: <em>"Cordia AI by AI-Gen Partners:
You're subscribed to your personal assistant. Message frequency varies. Msg &amp; data
rates may apply. Reply HELP for help, STOP to unsubscribe."</em></p>

<p style="margin-top:1.6rem"><a href="/opt-in">SMS Program Disclosure</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy Policy</a> &nbsp;|&nbsp; <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


@router.get("/opt-in", include_in_schema=False)
async def opt_in_page():
    return HTMLResponse(_OPT_IN)


@router.get("/consent", include_in_schema=False)
async def consent_form_page():
    return HTMLResponse(_CONSENT_FORM)


@router.get("/privacy", include_in_schema=False)
async def privacy_page():
    return HTMLResponse(_PRIVACY)


@router.get("/terms", include_in_schema=False)
async def terms_page():
    return HTMLResponse(_TERMS)
