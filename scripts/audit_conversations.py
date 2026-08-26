#!/usr/bin/env python3
"""Every exchange she had with Cord: what she asked, what came back, what happened next.

The question this answers is not "did it error" — it is "did she get what she
wanted, and if not, where did the ask and the answer miss each other." So the
output is the conversation itself, in order, with three things attached to each
exchange:

  HER     what she typed, and what the ask actually carried — was it open-ended
          or constrained, did it name a date, a place, a number, a budget, was
          it a fresh topic or a follow-up, and did it answer what Cord just asked
  CORD    what came back, how long it took, whether it offered options or asked
          a question, and whether the reply itself was sound or cut short
  SIGNAL  what she did next, which is the only honest measure of whether the
          answer landed: rephrased the same ask, corrected it, narrowed it,
          pushed back, or moved on satisfied

The style profile at the end is built from those, not from impressions. It says
how she actually uses it: how much she puts in an opening ask, whether she
answers questions put to her, how many turns a topic takes before it lands or
she drops it.

Reply health is still labelled, because a rephrase after a truncated answer
means something different from a rephrase after a complete one — you cannot read
her behaviour without knowing what she was reacting to.

Usage:
    ADMIN_API_SECRET=... python scripts/audit_conversations.py --since 2026-08-20
    python scripts/audit_conversations.py --file conversations-dump.json --phone +1615...
    python scripts/audit_conversations.py --file dump.json --summary-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

DEFAULT_URL = "https://cordia.aigenpartners.com"

# One implementation of the judgement, shared with the dashboard page at
# /health/conversations — two copies would drift and disagree about the same
# conversation.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.conversation_audit import (  # noqa: E402
    FIXES, build_exchanges, classify_reply, gap_text, summarise, text_of,
)


def _fetch(url: str, secret: str) -> list[dict]:
    import httpx
    with httpx.Client(timeout=60.0, headers={"X-Admin-Secret": secret}) as c:
        convs = c.get(f"{url}/api/v1/conversations").raise_for_status().json()["conversations"]
        out = []
        for conv in convs:
            r = c.get(f"{url}/api/v1/conversations/{conv['id']}/messages").raise_for_status()
            out.append({**conv, "messages": r.json()["messages"]})
    return out


def run(convs, phone, since, summary_only, width, who=""):
    for conv in convs:
        digits = re.sub(r"\D", "", conv.get("phone") or "")
        if phone and re.sub(r"\D", "", phone)[-10:] not in digits:
            continue
        msgs = [m for m in conv.get("messages", [])
                if not since or (m.get("created") or "") >= since]
        # The API gives a number, not a name. --who labels the thread.
        exchanges = build_exchanges(msgs, who or "They")
        if not exchanges:
            continue
        s = summarise(exchanges)

        print(f"\n{'='*width}\n{conv.get('phone','?')}   "
              f"{s['exchanges']} exchanges\n{'='*width}")

        if not summary_only:
            for i, ex in enumerate(exchanges, 1):
                carried = ", ".join(ex["prof"]["carried"]) or "nothing specific"
                open_tag = " · OPEN-ENDED" if ex["prof"]["open_ended"] else ""
                print(f"\n{'─'*width}\n  #{i}   {str(ex['at'])[:16].replace('T',' ')}")
                print(f"\n  {(who or 'THEY').upper()}  ({ex['prof']['words']} words · carried: {carried}{open_tag})")
                for line in (ex["her"] or "(empty)").splitlines() or [""]:
                    print(f"     {line}")
                rounds = (f"  ·  {ex['rounds']} tool round"
                          f"{'s' if ex['rounds'] != 1 else ''}" if ex.get("rounds") else "")
                print(f"\n  CORD  [{ex['verdict']}]"
                      f"{'  ·  ' + ex['took'] if ex['took'] else ''}{rounds}"
                      f"{'  · asked her something' if ex['cord_asked'] else ''}")
                for line in (ex["cord"] or "(nothing came back)").splitlines() or [""]:
                    print(f"     {line}")
                print(f"\n  → {ex['signal']}")

        p = s["pct"]
        print(f"\n\n{'='*width}\nHOW {(who or 'THEY').upper()} USES IT   "
              f"({s['exchanges']} exchanges)\n{'='*width}")
        print(f"\n  ask length      median {s['median_words']} words, "
              f"shortest {s['shortest']}, longest {s['longest']}")
        print(f"  {p['open_ended']:6.1%}  open-ended — no date, number, budget, place or people")
        print(f"  {p['rephrase']:6.1%}  re-asked something she had already asked")
        print(f"  {p['correcting']:6.1%}  corrected Cord — it solved the wrong problem")
        print(f"  {p['pushing_back']:6.1%}  pushed back — nothing arrived, or not what she meant")
        if s["cord_never_asked"]:
            print("\n  Cord never asked a question. That is the finding.")
        else:
            print(f"\n  Cord asked a question {s['answered'] + s['ignored']}× — "
                  f"answered {s['answered']}, skipped {s['ignored']}")
        print(f"  topics took a median of {s['median_topic_turns']} exchanges "
              f"(longest {s['longest_topic']})")

        print(f"\n{'─'*width}\n  WHAT CAME BACK")
        for verdict, c in s["replies"]:
            print(f"  {c:4d}  {c/(s['exchanges'] or 1):6.1%}  {verdict}")
        if s["unsound"]:
            print(f"\n  {s['unsound']} of {s['exchanges']} replies were not sound. "
                  "Fixes shipped at:")
            for stamp, label in FIXES:
                print(f"     {stamp}  {label}")
            print("  A rephrase after a cut-off reply is Cord's doing; "
                  "after a sound one it is a miss.")

        print(f"\n{'─'*width}\n  WHAT HAPPENED NEXT")
        for kind, c in s["signals"]:
            print(f"  {c:4d}  {kind}")
        print()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--secret", default=os.environ.get("ADMIN_API_SECRET", ""))
    p.add_argument("--file")
    p.add_argument("--phone", default="")
    p.add_argument("--since", default="")
    p.add_argument("--who", default="", help="whose thread this is, e.g. Cordia")
    p.add_argument("--summary-only", action="store_true")
    p.add_argument("--width", type=int, default=76)
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

    run(convs, a.phone, a.since, a.summary_only, a.width, a.who)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
