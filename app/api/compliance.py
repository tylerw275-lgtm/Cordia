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
<p><strong>Cordia AI</strong> is a personal SMS-based AI assistant operated by Crown Bakeries.<br>
Last updated: June 2026</p>

<h2>Information We Collect</h2>
<p>We collect your mobile phone number and the content of messages you send to the service. We do not collect any other personal information.</p>

<h2>How We Use Your Information</h2>
<p>Your phone number and messages are used solely to provide the Cordia AI assistant service. Message content is processed by Anthropic's Claude AI to generate responses. We do not sell or share your data with third parties for marketing purposes.</p>

<h2>Message Storage</h2>
<p>Conversation history is stored securely to provide context for future responses. You may request deletion of your conversation history at any time by contacting us.</p>

<h2>Opt-Out</h2>
<p>Reply <strong>STOP</strong> to any message to unsubscribe immediately. No further messages will be sent after opting out.</p>

<h2>Contact</h2>
<p>For questions or data requests: <a href="mailto:info@crownbakeries.com">info@crownbakeries.com</a></p>
</body>
</html>"""

_TERMS = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Terms of Service — Cordia AI</title>
<style>body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}</style>
</head>
<body>
<h1>Terms of Service</h1>
<p><strong>Cordia AI</strong> is operated by Crown Bakeries.<br>
Last updated: June 2026</p>

<h2>Service Description</h2>
<p>Cordia AI is a personal SMS-based AI assistant. By texting the service number, you agree to these terms.</p>

<h2>Messaging</h2>
<p>Message and data rates may apply. All messaging costs incurred by the operator are covered by Crown Bakeries — there is no charge to the end user beyond standard carrier rates.</p>

<h2>Opt-Out</h2>
<p>Reply <strong>STOP</strong> to unsubscribe at any time. Reply <strong>HELP</strong> for support information.</p>

<h2>Acceptable Use</h2>
<p>This service is for personal assistance purposes only. Do not use the service for unlawful purposes or to send unsolicited messages to others.</p>

<h2>Disclaimer</h2>
<p>This service is provided for informational and personal assistance purposes. For legal, medical, or financial decisions, always consult a qualified professional.</p>

<h2>Contact</h2>
<p><a href="mailto:info@crownbakeries.com">info@crownbakeries.com</a></p>
</body>
</html>"""

_OPT_IN = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Opt-In — Cordia AI</title>
<style>body{font-family:sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#222;line-height:1.6}</style>
</head>
<body>
<h1>Cordia AI — SMS Opt-In</h1>
<p>To use Cordia AI, simply text the service number. Your first message constitutes consent to receive SMS messages from this service.</p>
<p>Reply <strong>STOP</strong> at any time to unsubscribe. Reply <strong>HELP</strong> for support.</p>
<p>Message and data rates may apply. All service costs are covered by the operator.</p>
<p><a href="/privacy">Privacy Policy</a> | <a href="/terms">Terms of Service</a></p>
</body>
</html>"""


@router.get("/opt-in", include_in_schema=False)
async def opt_in_page():
    return HTMLResponse(_OPT_IN)


@router.get("/privacy", include_in_schema=False)
async def privacy_page():
    return HTMLResponse(_PRIVACY)


@router.get("/terms", include_in_schema=False)
async def terms_page():
    return HTMLResponse(_TERMS)
