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

FIXES = [
    ("2026-08-22T09:41", "Opus 5 live"),
    ("2026-08-22T10:34", "parsed_output 400 fixed"),
    ("2026-08-24T09:35", "reply truncation fixed"),
    ("2026-08-24T09:52", "answer-then-ask live"),
]

FALLBACK = "I'm on it - give me a moment and ask me again if you don't hear back."
FAILED = "Something went wrong on my end"
PROMISE = re.compile(r"\b(sending|send it|get it to you|in your inbox|shortly|"
                     r"in a (?:minute|moment|sec)|almost done|let me finish|right now)\b", re.I)

HAS_DATE = re.compile(
    r"\b(\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|tonight"
    r"|this (?:week|weekend|month)|next (?:week|weekend|month))\b", re.I)
HAS_NUMBER = re.compile(r"\b\d+\b")
HAS_MONEY = re.compile(r"[$£€]\s?\d|budget|per night|per person|under \d")
HAS_PROPER = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}\b")
HAS_PEOPLE = re.compile(r"\b(me and|my |our |for \d+ (?:people|of us)|kids|family|everyone|"
                        r"tom|karie|cordia)\b", re.I)

# What she does when an answer misses.
CORRECTION = re.compile(r"\b(no,|not what|i meant|actually|instead|rather|i said|"
                        r"that'?s not|wrong)\b", re.I)
# The trailing \b cannot match after "?", so those alternatives sit outside it.
PUSHBACK = re.compile(r"\b(didn'?t get|never (?:got|came)|still (?:waiting|nothing)|"
                      r"any(?:thing)? else|nothing|you there|anyone there)\b"
                      r"|hello\s*\?|^\s*\?+\s*$|\?\?", re.I)
STOPWORDS = set("a an the and or but of for to in on at is are was were be been with my "
                "our me i you it this that some any can could would please need want "
                "get got give find make do does help about from by as we us".split())


def _txt(content: str) -> str:
    if not content.lstrip().startswith("["):
        return content
    try:
        blocks = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(blocks, list):
        return content
    return " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text").strip()


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS}


def _overlap(a: str, b: str) -> float:
    ka, kb = _keywords(a), _keywords(b)
    return len(ka & kb) / len(ka | kb) if (ka or kb) else 0.0


def _when(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _gap(a: str, b: str) -> str:
    ta, tb = _when(a), _when(b)
    if not ta or not tb:
        return ""
    secs = (tb - ta).total_seconds()
    if secs < 90:
        return f"{int(secs)}s later"
    if secs < 5400:
        return f"{int(secs // 60)}m later"
    if secs < 172800:
        return f"{secs / 3600:.1f}h later"
    return f"{int(secs // 86400)}d later"


def classify_reply(text: str) -> str:
    t = text.strip()
    if not t:
        return "EMPTY"
    if FALLBACK in t:
        return "FALLBACK — model produced no text"
    if FAILED in t:
        return "DIED — error reply"
    if t.endswith((":", "-", "—", ",", ";")) or (len(t) > 40 and t[-1].isalnum()):
        return "CUT OFF mid-thought"
    if PROMISE.search(t):
        return "PROMISED a delivery"
    return "ok"


def profile_ask(text: str, prev_cord: str, prev_her: str) -> dict:
    words = text.split()
    carried = [name for name, rx in (("date", HAS_DATE), ("number", HAS_NUMBER),
                                     ("budget", HAS_MONEY), ("place/name", HAS_PROPER),
                                     ("who's coming", HAS_PEOPLE)) if rx.search(text)]
    asked_of_her = prev_cord.strip().endswith("?")
    return {
        "words": len(words),
        "carried": carried,
        "open_ended": not carried,
        "answered_cord": asked_of_her and len(words) > 3 and not PUSHBACK.search(text),
        "ignored_cord": asked_of_her and (len(words) <= 3 or bool(PUSHBACK.search(text))),
        "correcting": bool(CORRECTION.search(text)),
        "pushing_back": bool(PUSHBACK.search(text)),
        "rephrase": _overlap(prev_her, text) > 0.34 if prev_her else False,
    }


def _signal(nxt_prof: dict | None, gap: str) -> tuple[str, str]:
    """(kind, line). The kind aggregates; the line is what you read."""
    if nxt_prof is None:
        return "topic ended here", "no further message — topic ended here"
    if nxt_prof["pushing_back"]:
        return ("pushed back — the answer did not land",
                f"SHE PUSHED BACK {gap} — the answer did not land")
    if nxt_prof["correcting"]:
        return ("corrected it — Cord solved the wrong problem",
                f"SHE CORRECTED IT {gap} — Cord solved the wrong problem")
    if nxt_prof["rephrase"]:
        return ("asked again — same ask, reworded",
                f"SHE ASKED AGAIN {gap} — same ask, reworded")
    if nxt_prof["ignored_cord"]:
        return ("Cord asked, she did not answer",
                f"CORD ASKED, SHE DID NOT ANSWER {gap}")
    if nxt_prof["answered_cord"]:
        return "answered Cord's question", f"she answered Cord's question {gap}"
    quiet = (gap.endswith("d later")
             or (gap.endswith("h later") and float(gap.split("h")[0]) >= 6))
    if quiet:
        return ("went quiet", f"SHE WENT QUIET — nothing more for {gap.replace(' later','')}")
    return "moved on", f"she moved on {gap}"


def _fetch(url: str, secret: str) -> list[dict]:
    import httpx
    with httpx.Client(timeout=60.0, headers={"X-Admin-Secret": secret}) as c:
        convs = c.get(f"{url}/api/v1/conversations").raise_for_status().json()["conversations"]
        out = []
        for conv in convs:
            r = c.get(f"{url}/api/v1/conversations/{conv['id']}/messages").raise_for_status()
            out.append({**conv, "messages": r.json()["messages"]})
    return out


def run(convs, phone, since, summary_only, width):
    style, replies, signals = Counter(), Counter(), Counter()
    ask_words, topic_turns, exchanges = [], [], 0

    for conv in convs:
        if phone and phone[-10:] not in re.sub(r"\D", "", conv.get("phone") or ""):
            continue
        msgs = [m for m in conv.get("messages", [])
                if not since or (m.get("created") or "") >= since]
        if not msgs:
            continue

        print(f"\n{'='*width}\n{conv.get('phone','?')}   {len(msgs)} messages\n{'='*width}")

        # Pair each of her messages with the reply that followed it.
        pairs, prev_cord, prev_her = [], "", ""
        for i, m in enumerate(msgs):
            if m["role"] != "user":
                prev_cord = _txt(m.get("content", ""))
                continue
            text = _txt(m.get("content", ""))
            reply, reply_at = "", ""
            for nxt in msgs[i + 1:]:
                if nxt["role"] == "assistant":
                    reply, reply_at = _txt(nxt.get("content", "")), nxt.get("created", "")
                    break
                break
            pairs.append({"at": m.get("created", ""), "her": text, "cord": reply,
                          "cord_at": reply_at,
                          "prof": profile_ask(text, prev_cord, prev_her)})
            prev_her, prev_cord = text, reply

        run_len = 0
        for n, p in enumerate(pairs):
            prof = p["prof"]
            nxt = pairs[n + 1]["prof"] if n + 1 < len(pairs) else None
            gap = _gap(p["at"], pairs[n + 1]["at"]) if n + 1 < len(pairs) else ""
            verdict = classify_reply(p["cord"]) if p["cord"] else "NO REPLY AT ALL"
            kind, sig = _signal(nxt, gap)

            exchanges += 1
            ask_words.append(prof["words"])
            replies[verdict] += 1
            signals[kind] += 1
            for tag in ("open_ended", "correcting", "pushing_back", "rephrase",
                        "answered_cord", "ignored_cord"):
                if prof[tag]:
                    style[tag] += 1
            run_len += 1
            if nxt is None or not (nxt["rephrase"] or nxt["correcting"] or nxt["pushing_back"]):
                topic_turns.append(run_len)
                run_len = 0

            if summary_only:
                continue

            carried = ", ".join(prof["carried"]) or "nothing specific"
            print(f"\n{'─'*width}\n  #{n+1}   {p['at'][:16].replace('T',' ')}")
            print(f"\n  HER  ({prof['words']} words · carried: {carried}"
                  f"{' · OPEN-ENDED' if prof['open_ended'] else ''})")
            for line in (p["her"] or "(empty)").splitlines() or [""]:
                print(f"     {line}")
            took = _gap(p["at"], p["cord_at"]) if p["cord_at"] else ""
            print(f"\n  CORD  [{verdict}]{'  ·  ' + took if took else ''}"
                  f"{'  · asked her something' if p['cord'].strip().endswith('?') else ''}")
            for line in (p["cord"] or "(nothing came back)").splitlines() or [""]:
                print(f"     {line}")
            print(f"\n  → {sig}")

    # ---- style profile -----------------------------------------------------
    n = exchanges or 1
    print(f"\n\n{'='*width}\nHOW SHE USES IT   ({exchanges} exchanges)\n{'='*width}")
    if ask_words:
        srt = sorted(ask_words)
        print(f"\n  ask length      median {srt[len(srt)//2]} words, "
              f"shortest {srt[0]}, longest {srt[-1]}")
    print(f"  {style['open_ended']/n:6.1%}  open-ended — no date, number, budget, place or people")
    print(f"  {style['rephrase']/n:6.1%}  re-asked something she had already asked")
    print(f"  {style['correcting']/n:6.1%}  corrected Cord — it solved the wrong problem")
    print(f"  {style['pushing_back']/n:6.1%}  pushed back — nothing arrived, or not what she meant")
    answered, ignored = style["answered_cord"], style["ignored_cord"]
    if answered + ignored:
        print(f"\n  Cord asked her something {answered+ignored}× — "
              f"she answered {answered}, skipped {ignored}")
    else:
        print("\n  Cord never asked her a question. That is the finding.")
    if topic_turns:
        print(f"  topics took a median of {sorted(topic_turns)[len(topic_turns)//2]} "
              f"exchanges (longest {max(topic_turns)})")

    print(f"\n{'─'*width}\n  WHAT CAME BACK")
    for verdict, c in replies.most_common():
        print(f"  {c:4d}  {c/n:6.1%}  {verdict}")
    bad = sum(c for v, c in replies.items() if v != "ok")
    if bad:
        print(f"\n  {bad} of {n} replies were not sound. Fixes shipped at:")
        for stamp, label in FIXES:
            print(f"     {stamp}  {label}")
        print("  A rephrase after a cut-off reply is Cord's doing; after a sound one it is a miss.")

    print(f"\n{'─'*width}\n  WHAT SHE DID NEXT")
    for sig, c in signals.most_common():
        print(f"  {c:4d}  {sig}")
    print()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--secret", default=os.environ.get("ADMIN_API_SECRET", ""))
    p.add_argument("--file")
    p.add_argument("--phone", default="")
    p.add_argument("--since", default="")
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

    run(convs, a.phone, a.since, a.summary_only, a.width)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
