#!/usr/bin/env python3
"""Read the stored conversations and say what actually went wrong in them.

"It doesn't work right" is a report about an experience, not a diagnosis. This
separates the two things that produce it:

  1. Replies Cord got wrong — truncated, failed, or promised and never
     delivered. These have exact fingerprints in the stored text.
  2. How the ask was phrased — one-liners with no dates, places or names give
     the model nothing to work with, and questions that never get answered
     leave it guessing.

Both are real, and blaming the second for the first is how a client gets told
they are using it wrong when they are not. So the report dates every finding:
anything before a fix shipped belongs to the bug, not the person.

Defaults to counts and classifications, never the message text — this is
somebody's private correspondence with their assistant. Pass --verbatim when
you actually need to read it.

Usage:
    ADMIN_API_SECRET=... python scripts/audit_conversations.py
    ADMIN_API_SECRET=... python scripts/audit_conversations.py --phone +1615... --since 2026-08-22
    python scripts/audit_conversations.py --file dump.json --verbatim
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_URL = "https://cordia.aigenpartners.com"

# Fixes that changed what a reply looks like. A complaint dated before one of
# these is evidence about the bug, not about the person typing.
FIXES = [
    ("2026-08-22T09:41Z", "Opus 5 went live"),
    ("2026-08-22T10:34Z", "parsed_output 400 fixed (turns died outright before this)"),
    ("2026-08-24T09:35Z", "reply truncation fixed (every answer cut at the first pause before this)"),
    ("2026-08-24T09:52Z", "answer-then-ask added"),
]

FALLBACK = "I'm on it - give me a moment and ask me again if you don't hear back."
FAILED = "Something went wrong on my end"
PROMISE = re.compile(
    r"\b(sending|send it|get it to you|in your inbox|shortly|in a (?:minute|moment|sec)|"
    r"almost done|working on it|let me finish|right now)\b", re.I)

# Signals that an ask carried something to work with.
HAS_DATE = re.compile(
    r"\b(\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}"
    # Full or abbreviated month names only — "oct[a-z]*" also matched "Octopus".
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|tonight"
    r"|this (?:week|weekend|month)|next (?:week|weekend|month))\b", re.I)
HAS_NUMBER = re.compile(r"\b\d+\b")
HAS_MONEY = re.compile(r"[$£€]\s?\d|budget|per night|per person")
HAS_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")  # a name or place mid-sentence


def _txt(content: str) -> str:
    """Stored content is plain text or a JSON list of blocks."""
    if not content.lstrip().startswith("["):
        return content
    try:
        blocks = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(blocks, list):
        return content
    return " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text")


def classify_reply(text: str) -> str:
    t = text.strip()
    if not t:
        return "empty"
    if FALLBACK in t:
        return "fallback (model produced no text)"
    if FAILED in t:
        return "turn died (error reply)"
    # Truncation fingerprint: stops where a list or a sentence should continue.
    if t.endswith((":", "-", "—", ",", ";")) or (t[-1].isalnum() and len(t) > 40):
        return "cut off mid-thought"
    if PROMISE.search(t):
        return "promised a delivery"
    return "ok"


def profile_ask(text: str) -> dict:
    words = text.split()
    return {
        "chars": len(text),
        "words": len(words),
        "one_liner": len(words) <= 12,
        "has_date": bool(HAS_DATE.search(text)),
        "has_number": bool(HAS_NUMBER.search(text)),
        "has_money": bool(HAS_MONEY.search(text)),
        "has_name_or_place": bool(HAS_PROPER.search(text)),
        "is_question": text.strip().endswith("?"),
    }


def _fetch(url: str, secret: str) -> list[dict]:
    import httpx
    headers = {"X-Admin-Secret": secret}
    with httpx.Client(timeout=60.0, headers=headers) as c:
        convs = c.get(f"{url}/api/v1/conversations").raise_for_status().json()["conversations"]
        out = []
        for conv in convs:
            msgs = c.get(f"{url}/api/v1/conversations/{conv['id']}/messages")
            out.append({**conv, "messages": msgs.raise_for_status().json()["messages"]})
    return out


def report(convs: list[dict], phone: str, since: str, verbatim: bool) -> None:
    replies, asks, broken, unanswered = Counter(), [], [], 0
    total_msgs = 0

    for conv in convs:
        if phone and phone[-10:] not in (conv.get("phone") or ""):
            continue
        msgs = conv.get("messages", [])
        for i, m in enumerate(msgs):
            when = m.get("created", "")
            if since and when < since:
                continue
            text = _txt(m.get("content", ""))
            total_msgs += 1
            if m["role"] == "user":
                asks.append(profile_ask(text))
                if verbatim:
                    print(f"  [{when[:16]}] HER: {text[:400]}")
            else:
                verdict = classify_reply(text)
                replies[verdict] += 1
                if verdict != "ok":
                    broken.append((when, verdict, text[:160]))
                # Cord asked something and she moved on without answering.
                if text.strip().endswith("?") and i + 1 < len(msgs):
                    nxt = _txt(msgs[i + 1].get("content", ""))
                    if msgs[i + 1]["role"] == "user" and len(nxt.split()) <= 3:
                        unanswered += 1
                if verbatim:
                    print(f"  [{when[:16]}] CORD [{verdict}]: {text[:400]}")

    print("\n" + "=" * 68)
    print(f"{total_msgs} messages across {len(convs)} conversation(s)")
    print("=" * 68)

    print("\nWHAT CORD SENT BACK")
    total_replies = sum(replies.values()) or 1
    for verdict, n in replies.most_common():
        print(f"  {n:4d}  {n/total_replies:5.1%}  {verdict}")

    if broken:
        print("\nWHEN THE BROKEN ONES HAPPENED — against the fixes")
        for stamp, label in FIXES:
            n = sum(1 for w, _, _ in broken if w and w < stamp)
            print(f"  {n:4d} before {stamp}  {label}")
        print("\n  most recent bad replies:")
        for when, verdict, snippet in sorted(broken)[-8:]:
            print(f"    [{when[:16]}] {verdict}: {snippet!r}")

    if asks:
        n = len(asks)
        print(f"\nHOW SHE ASKS  ({n} messages)")
        print(f"  median length          {sorted(a['words'] for a in asks)[n//2]} words")
        for key, label in (("one_liner", "12 words or fewer"),
                           ("has_date", "names a date or day"),
                           ("has_name_or_place", "names a person or place"),
                           ("has_number", "includes a number"),
                           ("has_money", "mentions budget or price")):
            hits = sum(1 for a in asks if a[key])
            print(f"  {hits/n:5.1%}  {label}")
        print(f"\n  Cord asked a question and got a non-answer: {unanswered}×")

    print("\nREAD IT THIS WAY")
    print("  Rows above the newest fix line are Cord's fault, not hers. Only the")
    print("  'HOW SHE ASKS' block and the unanswered-question count say anything")
    print("  about prompting — and a one-liner is only a problem if Cord failed")
    print("  to ask the follow-up, which is what answer-then-ask now does.\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--secret", default=os.environ.get("ADMIN_API_SECRET", ""))
    p.add_argument("--file", help="a saved JSON dump instead of a live fetch")
    p.add_argument("--phone", default="", help="only this number")
    p.add_argument("--since", default="", help="ISO date, e.g. 2026-08-22")
    p.add_argument("--verbatim", action="store_true", help="print the message text")
    a = p.parse_args()

    if a.file:
        convs = json.load(open(a.file))
    else:
        if not a.secret:
            print("Set ADMIN_API_SECRET (or pass --secret).", file=sys.stderr)
            return 2
        convs = _fetch(a.url.rstrip("/"), a.secret)
        with open("conversations-dump.json", "w") as f:
            json.dump(convs, f, indent=2)
        print("Saved conversations-dump.json")

    report(convs, a.phone, a.since, a.verbatim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
