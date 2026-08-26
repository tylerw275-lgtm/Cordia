"""Read a stored conversation and say where the ask and the answer missed.

The question is not "did it error" but "did she get what she wanted." So each
exchange carries three things: what the ask actually carried, what came back,
and what she did next — which is the only honest measure of whether the answer
landed.

Reply health is labelled alongside, because behaviour cannot be read without
knowing what she was reacting to. A rephrase after a truncated answer means
something different from a rephrase after a sound one.

Pure functions over rows. The dashboard page and scripts/audit_conversations.py
both call this, so there is one implementation of the judgement rather than two
that drift.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime

# Fixes that changed what a reply looks like. A complaint from before one of
# these is evidence about the bug, not about the person typing.
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
HAS_PEOPLE = re.compile(r"\b(me and|my |our |for \d+ (?:people|of us)|kids|family|everyone)\b", re.I)

CORRECTION = re.compile(r"\b(no,|not what|i meant|actually|instead|rather|i said|"
                        r"that'?s not|wrong)\b", re.I)
# The trailing \b cannot match after "?", so those alternatives sit outside it.
PUSHBACK = re.compile(r"\b(didn'?t get|never (?:got|came)|still (?:waiting|nothing)|"
                      r"any(?:thing)? else|nothing|you there|anyone there)\b"
                      r"|hello\s*\?|^\s*\?+\s*$|\?\?", re.I)
STOPWORDS = set("a an the and or but of for to in on at is are was were be been with my "
                "our me i you it this that some any can could would please need want "
                "get got give find make do does help about from by as we us".split())


def text_of(content: str) -> str:
    """Stored content is plain text or a JSON list of blocks."""
    if not content or not content.lstrip().startswith("["):
        return content or ""
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


def overlap(a: str, b: str) -> float:
    ka, kb = _keywords(a), _keywords(b)
    return len(ka & kb) / len(ka | kb) if (ka or kb) else 0.0


def _dt(stamp) -> datetime | None:
    if isinstance(stamp, datetime):
        return stamp
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def gap_text(a, b) -> str:
    ta, tb = _dt(a), _dt(b)
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
    t = (text or "").strip()
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
    words = (text or "").split()
    carried = [name for name, rx in (("date", HAS_DATE), ("number", HAS_NUMBER),
                                     ("budget", HAS_MONEY), ("place/name", HAS_PROPER),
                                     ("who's coming", HAS_PEOPLE)) if rx.search(text or "")]
    asked_of_her = (prev_cord or "").strip().endswith("?")
    pushing = bool(PUSHBACK.search(text or ""))
    return {
        "words": len(words),
        "carried": carried,
        "open_ended": not carried,
        "answered_cord": asked_of_her and len(words) > 3 and not pushing,
        "ignored_cord": asked_of_her and (len(words) <= 3 or pushing),
        "correcting": bool(CORRECTION.search(text or "")),
        "pushing_back": pushing,
        "rephrase": overlap(prev_her, text or "") > 0.34 if prev_her else False,
    }


def signal(nxt_prof: dict | None, gap: str) -> tuple[str, str]:
    """(kind, line). The kind aggregates; the line is what you read."""
    if nxt_prof is None:
        return "topic ended here", "no further message — topic ended here"
    if nxt_prof["pushing_back"]:
        return ("pushed back — the answer did not land",
                f"She pushed back {gap} — the answer did not land")
    if nxt_prof["correcting"]:
        return ("corrected it — Cord solved the wrong problem",
                f"She corrected it {gap} — Cord solved the wrong problem")
    if nxt_prof["rephrase"]:
        return ("asked again — same ask, reworded",
                f"She asked again {gap} — same ask, reworded")
    if nxt_prof["ignored_cord"]:
        return "Cord asked, she did not answer", f"Cord asked, she did not answer {gap}"
    if nxt_prof["answered_cord"]:
        return "answered Cord's question", f"She answered Cord's question {gap}"
    quiet = (gap.endswith("d later")
             or (gap.endswith("h later") and float(gap.split("h")[0]) >= 6))
    if quiet:
        return "went quiet", f"She went quiet — nothing more for {gap.replace(' later', '')}"
    return "moved on", f"She moved on {gap}"


def build_exchanges(messages: list[dict]) -> list[dict]:
    """Pair each message she sent with the reply it drew, in order.

    `messages` are dicts with role, content and created, oldest first.
    """
    pairs, prev_cord, prev_her = [], "", ""
    for i, m in enumerate(messages):
        if m.get("role") != "user":
            continue
        her = text_of(m.get("content", ""))

        # A turn that used tools writes several rows: assistant[tool_use] →
        # tool[results] → … → assistant[the answer]. Stopping at the first one
        # reported "NO REPLY AT ALL" for every turn that did any work, which is
        # exactly the turns worth reading. Walk to the next thing she said,
        # keeping the last assistant text — that is the one she was actually
        # sent, since the loop returns only its final text.
        reply, reply_at, rounds = "", None, 0
        for nxt in messages[i + 1:]:
            role = nxt.get("role")
            if role == "user":
                break
            if role != "assistant":
                continue          # tool results are not something Cord said
            spoken = text_of(nxt.get("content", ""))
            if spoken:
                reply, reply_at = spoken, nxt.get("created")
            else:
                rounds += 1       # an assistant turn that only called tools

        pairs.append({
            "at": m.get("created"), "her": her, "cord": reply, "cord_at": reply_at,
            "rounds": rounds,
            "prof": profile_ask(her, prev_cord, prev_her),
            "verdict": classify_reply(reply) if reply else "NO REPLY AT ALL",
            "cord_asked": reply.strip().endswith("?"),
            "took": gap_text(m.get("created"), reply_at) if reply_at else "",
        })
        prev_her, prev_cord = her, reply

    for n, p in enumerate(pairs):
        nxt = pairs[n + 1]["prof"] if n + 1 < len(pairs) else None
        gap = gap_text(p["at"], pairs[n + 1]["at"]) if n + 1 < len(pairs) else ""
        p["signal_kind"], p["signal"] = signal(nxt, gap)
    return pairs


def mark_delivery(exchanges: list[dict], sent_at: list, window_s: int = 900) -> None:
    """Flag replies that were written but never actually reached her.

    An assistant row is persisted whether or not the text ever left the
    building. During the days the app was half-deployed, replies were composed,
    stored, and never sent — so the audit showed a tidy answer to a message
    that got silence. `sms_service.send_sms` records an `sms_out` usage event
    only after a real send, which makes that ledger the honest record of what
    was delivered.

    No ledger rows at all means the ledger is not usable here (an email thread,
    or a window before billing was recorded); the flag is left alone rather
    than accusing every reply of never arriving.
    """
    if not sent_at:
        return
    stamps = sorted(t for t in (_dt(x) for x in sent_at) if t)
    if not stamps:
        return
    import bisect
    for ex in exchanges:
        made = _dt(ex.get("cord_at"))
        if not ex.get("cord") or not made:
            continue
        i = bisect.bisect_left(stamps, made)
        near = any((stamps[j] - made).total_seconds() <= window_s
                   for j in (i, i - 1) if 0 <= j < len(stamps)
                   and (stamps[j] - made).total_seconds() >= -window_s)
        if not near:
            ex["verdict"] = "NEVER SENT — written but not delivered"


def summarise(exchanges: list[dict]) -> dict:
    """The style profile, counted from the signals rather than eyeballed."""
    n = len(exchanges) or 1
    style, replies, signals = Counter(), Counter(), Counter()
    words, topic_turns, run = [], [], 0

    for n_i, ex in enumerate(exchanges):
        prof = ex["prof"]
        words.append(prof["words"])
        replies[ex["verdict"]] += 1
        signals[ex["signal_kind"]] += 1
        for tag in ("open_ended", "correcting", "pushing_back", "rephrase",
                    "answered_cord", "ignored_cord"):
            if prof[tag]:
                style[tag] += 1
        run += 1
        nxt = exchanges[n_i + 1]["prof"] if n_i + 1 < len(exchanges) else None
        if nxt is None or not (nxt["rephrase"] or nxt["correcting"] or nxt["pushing_back"]):
            topic_turns.append(run)
            run = 0

    srt = sorted(words)
    return {
        "exchanges": len(exchanges),
        "median_words": srt[len(srt) // 2] if srt else 0,
        "shortest": srt[0] if srt else 0,
        "longest": srt[-1] if srt else 0,
        "pct": {k: style[k] / n for k in
                ("open_ended", "rephrase", "correcting", "pushing_back")},
        "answered": style["answered_cord"],
        "ignored": style["ignored_cord"],
        "cord_never_asked": (style["answered_cord"] + style["ignored_cord"]) == 0,
        "median_topic_turns": (sorted(topic_turns)[len(topic_turns) // 2]
                               if topic_turns else 0),
        "longest_topic": max(topic_turns) if topic_turns else 0,
        "replies": replies.most_common(),
        "signals": signals.most_common(),
        "unsound": sum(c for v, c in replies.items() if v != "ok"),
    }
