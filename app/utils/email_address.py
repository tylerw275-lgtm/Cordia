"""One way to reduce an email address to something comparable.

`Cordia <Tyler@ai-genpartners.com>` and `tyler@ai-genpartners.com` are the same
mailbox. Comparing the raw strings says they are not, and that is not a
hypothetical: OWNER_EMAIL was set to the display-name form, so every reply
Cordia sent resolved to `unknown` and was dropped without a word. Outbound kept
working the whole time, because a display name in a `To:` header is perfectly
valid — which is why it looked like "it emails me but never replies".

The same shape of bug as the four phone normalisers this codebase already
collapsed into one. An address arrives from a config variable, a webhook
payload, a dashboard form or a `From:` header, and only one of those four is
reliably bare.
"""
import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def normalize_email(value) -> str:
    """The bare, lowercased address inside `value`, or "" if there isn't one.

    Accepts what actually turns up: a bare address, a `Name <addr>` header, a
    dict from a webhook payload, None. Never raises — a caller comparing
    identities should get a miss, not an exception.
    """
    if isinstance(value, dict):
        value = value.get("email") or value.get("address") or ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    match = _EMAIL_RE.search(str(value or ""))
    return match.group(0).lower() if match else ""


def emails_match(a, b) -> bool:
    """True when both name the same mailbox. Empty never matches empty."""
    left, right = normalize_email(a), normalize_email(b)
    return bool(left) and left == right
