"""GSM-03.38, because the alphabet a text is written in decides what it costs.

A carrier bills per segment. A message in the GSM 7-bit alphabet fits 160
characters per segment; one character outside it re-encodes the *whole* message
as UCS-2 and the limit drops to 70. So a 200-character morning brief is one
segment written one way and three written another, and the difference is
usually a single em dash nobody chose deliberately.

The holding notes were already fixed for this, with a comment explaining the
cost. Every proactive message the scheduler sends still had one: an em dash in
the birthday note, an arrow in the flight alert, a house emoji on the Naples
capture. Those go out every day.

Two jobs here:

- `is_gsm` decides the segment size honestly. The old test — "any character
  above ASCII" — was wrong in both directions: `£`, `é`, `Ö` and `ñ` are all in
  the GSM alphabet and cost nothing extra, so a French or Spanish name was
  billed as if it had tripled the message.
- `to_gsm` rewrites the punctuation that has an obvious equivalent. It touches
  dashes, quotes, ellipses and arrows and nothing else. It will never
  transliterate a letter: a name is worth more than a segment.
"""

# The GSM 7-bit basic character set, plus the extension table (each extension
# character costs two septets, which the segment maths below accounts for).
_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_EXTENDED = "^{}\\[~]|€"

GSM_CHARS = frozenset(_BASIC) | frozenset(_EXTENDED)

# Only punctuation, and only where the replacement means the same thing.
_SUBSTITUTIONS = {
    "—": "-",       # em dash
    "–": "-",       # en dash
    "‒": "-",       # figure dash
    "−": "-",       # minus sign
    "‘": "'",       # left single quote
    "’": "'",       # right single quote / apostrophe
    "‚": "'",
    "“": '"',       # left double quote
    "”": '"',       # right double quote
    "„": '"',
    "…": "...",     # ellipsis
    "→": "->",      # rightwards arrow
    "←": "<-",
    "•": "-",       # bullet
    " ": " ",       # non-breaking space
    " ": " ",       # narrow no-break space
    " ": " ",       # thin space
    "­": "",        # soft hyphen
    "′": "'",       # prime
    "″": '"',
    "·": "-",       # middle dot
    "⁄": "/",       # fraction slash
    "™": "(TM)",
    "®": "(R)",
    "©": "(C)",
}


def is_gsm(text: str) -> bool:
    """True if every character can be sent in the 7-bit alphabet."""
    return all(c in GSM_CHARS for c in text or "")


def to_gsm(text: str) -> str:
    """Replace non-GSM punctuation with its GSM equivalent.

    Anything without an obvious equivalent — an emoji, a Chinese character, an
    accent outside the GSM set — is left exactly as it is. Mangling a person's
    name to save a segment would be a bad trade, and a message that still needs
    UCS-2 is simply billed as UCS-2.
    """
    if not text:
        return text
    return "".join(_SUBSTITUTIONS.get(c, c) for c in text)


def septets(text: str) -> int:
    """Length in 7-bit units. Extension-table characters take two."""
    return sum(2 if c in _EXTENDED else 1 for c in text)
