"""One table for what a message is about.

There were three, all matched by naive substring, and the same word appeared in
several of them with different consequences:

- `CONTEXT_KEYWORDS` chose which module brief went into the system prompt
- `_DEEP_WORK_KEYWORDS` chose the token budget, the reasoning effort, and which
  holding note Cord texted while it worked
- `_MATCH_HINTS` chose which interview playbook a project started from

Substring matching on short words is worse than it sounds. `"rent"` matched
*current*, *parent* and *different*, so "I'll be at a different address" pulled in
the sixty-line commercial-landlord brief. `"cam"` matched *became* and *camera*.
`"show"` matched *shower*. `"book"` matched *Facebook*. Every false positive on
the deep-work table also silently quadrupled the token budget.

Two consequences of the split were worse than the matching. `event_planning`
existed in two tables with contradictory instructions — the prompt block said
"Build the full plan, not tips" while the playbook said "Do NOT answer the
request yet" — and both could be in context at once. And `lease_review` had the
longest brief in the codebase with the *smallest* budget, because it was in the
context table and not the deep-work one, so the analysis very likely truncated.

Matching is on word boundaries, so a keyword matches a word and not the inside of
one. Phrases match across whitespace runs.
"""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    """What a message is about, and everything that follows from that.

    One row means the three decisions can no longer disagree: a subject either
    deserves a module brief, a deeper budget and a playbook, or it does not.
    """
    name: str
    words: tuple[str, ...]
    # Key into MODULE_CONTEXTS. None means no module brief for this subject.
    context: str | None = None
    # Bigger token budget and higher reasoning effort.
    deep: bool = False
    # Key into playbooks.PLAYBOOKS, for starting a project interview.
    playbook: str | None = None
    _pattern: re.Pattern = field(default=None, compare=False, repr=False)


def _compile(words: tuple[str, ...]) -> re.Pattern:
    r"""A word-boundary alternation over every keyword.

    `\b` on both ends is what stops "rent" matching "different". Phrases allow a
    run of whitespace between words so "set   up" and a line break both match.
    """
    parts = [r"\s+".join(re.escape(w) for w in phrase.split()) for phrase in words]
    parts.sort(key=len, reverse=True)          # longest alternative wins
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def _intent(name, words, **kw) -> Intent:
    return Intent(name=name, words=words, _pattern=_compile(words), **kw)


# Ordered most specific first: the first match wins, so a lease question is a
# lease question even though "property" is also a word people use about houses.
INTENTS: tuple[Intent, ...] = (
    _intent(
        "lease_review",
        # Deep, finally. It carries the longest brief in the codebase and used
        # to run on the small budget because it was missing from the other table.
        context="lease_review", deep=True,
        words=(
            "lease", "leases", "rent", "rents", "tenant", "tenants", "landlord",
            "clause", "clauses", "renewal", "sublet", "escalation", "estoppel",
            "square feet", "square footage", "build-out", "buildout",
            "tenant improvement", "cam", "cam charges", "triple net", "nnn",
        ),
    ),
    _intent(
        "place_setup",
        deep=True, playbook="place_setup",
        words=(
            "pack", "packing", "furnish", "furnishing", "outfit", "outfitting",
            "move in", "moving in", "set up the", "setting up the",
            "second home", "new house", "new home", "stock the",
        ),
    ),
    _intent(
        "service_sourcing",
        deep=True, playbook="service_sourcing",
        words=(
            "car service", "town car", "driver", "limo", "chauffeur",
            "caterer", "catering", "cleaner", "cleaning service", "contractor",
            "find me a", "book a", "booking a", "hire", "quote", "quotes",
            "cheapest", "best price", "price out", "how much would",
        ),
    ),
    _intent(
        "event_planning",
        context="event_planning", deep=True, playbook="event_planning",
        words=(
            "party", "parties", "dinner party", "event", "gala", "fundraiser",
            "celebration", "reception", "comedy night", "host", "hosting",
            # Not bare "shower" — word boundaries cannot tell a baby shower from
            # a shower, and this table used to route "taking a shower" to the
            # event producer brief.
            "baby shower", "bridal shower", "wedding shower",
            "reunion", "night out", "run of show",
        ),
    ),
    _intent(
        "trip_planning",
        context="trip_planning", deep=True,
        words=(
            "flight", "flights", "fly", "flying", "hotel", "hotels", "trip",
            "trips", "travel", "traveling", "travelling", "airport", "vacation",
            "cruise", "itinerary", "thanksgiving", "layover", "red-eye",
        ),
    ),
    _intent(
        "family_coordination",
        context="family_coordination",
        words=(
            "family", "gather", "gathering", "get together", "birthday",
            "anniversary", "grandkid", "grandkids", "grandchild",
            "grandchildren", "reunion", "schedule",
        ),
    ),
    _intent(
        "research_brief",
        deep=True, playbook="research_brief",
        words=("research", "look into", "dig into", "compare", "comparison"),
    ),
)


# Words that mean "this is real work" without saying what the work is about.
# They set the budget and nothing else — there is no brief or playbook to pick.
_DEEP_ONLY = _compile((
    "plan", "planning", "organize", "organise", "brainstorm", "ideas",
    "options", "recommend", "recommendation", "recommendations", "draft",
    "write up", "put together", "prepare", "help me with", "look up",
    "search for", "shop for", "vendor", "vendors", "price", "pricing", "cost",
    "costs", "budget", "figure out", "work out", "sort out",
))


def match(message: str) -> Intent | None:
    """The intent of a message, or None when nothing matches.

    A miss is the normal case and costs nothing: the project engine derives an
    interview from first principles, and an ordinary message gets the ordinary
    budget.
    """
    text = message or ""
    for intent in INTENTS:
        if intent._pattern.search(text):
            return intent
    return None


def detect_context(message: str) -> str | None:
    """Which module brief belongs in the system prompt, if any."""
    intent = match(message)
    return intent.context if intent else None


def is_deep_work(message: str, context_hint: str | None = None) -> bool:
    """Whether this deserves the bigger budget and higher reasoning effort.

    `context_hint` is honoured when a caller already resolved one, so a turn does
    not classify the same message twice and cannot reach two different answers.
    """
    if context_hint:
        for intent in INTENTS:
            if intent.name == context_hint:
                return intent.deep
    intent = match(message)
    if intent is not None:
        return intent.deep
    return bool(_DEEP_ONLY.search(message or ""))


def playbook_for(message: str) -> str | None:
    """Which interview family a project should start from, or None to derive one."""
    intent = match(message)
    return intent.playbook if intent else None
