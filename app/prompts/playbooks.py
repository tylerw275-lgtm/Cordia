"""How Cord turns a casual ask into an expert interview.

The point of this module is that Cordia's questions arrive as one-liners — "what
should I pack for the Naples house", "find me a car for Tom on Thursday" — and a
one-liner answered literally produces exactly the generic reply that left her
unimpressed with AI. The value is in the interview and the research, not in the
model's recall.

Two of her asks are known. The third is not, so this is deliberately **not** a
lookup table of pre-written question lists. It is a small set of broad families
plus a method for deriving an interview for anything else. `INTAKE_DIMENSIONS` is
the part that generalises: those axes change the answer regardless of subject,
and forgetting them is what makes an answer generic.
"""

# The axes that almost always change an answer and are easy to forget. Cord
# walks these to build an interview for an ask no playbook anticipated.
INTAKE_DIMENSIONS = [
    ("place", "Where. Climate through the year, seasonality, local norms and dress, "
              "what is cheap to buy there versus worth shipping or bringing."),
    ("time", "When. Exact dates and times, lead time, deadlines, how long it lasts, "
             "and what is seasonal about that window."),
    ("people", "Who is involved — how many, their ages and constraints, who else "
               "needs to be happy with the outcome."),
    ("scale", "How big. Square footage, headcount, number of nights, quantity — the "
              "number that changes what a good answer looks like."),
    ("purpose", "What it is actually for. The same house furnished for hosting and "
                "for hiding out needs different things."),
    ("constraints", "Budget posture, hard requirements, dealbreakers, and anything "
                    "already decided that cannot move."),
    ("existing", "What is already there or already booked, so nothing is duplicated "
                 "and nothing is assumed missing."),
]


def dimensions_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in INTAKE_DIMENSIONS)


# Handed back when no family matches. The obligation to design questions arrives
# as tool data rather than prompt text, and Cord must commit the questions with
# save_project_questions before answering — so a derived interview is as durable
# and inspectable as a pre-written one.
QUESTION_DESIGN_BRIEF = """No stock interview covers this ask, so design one.

Work through these axes and keep only the ones that would genuinely change your
answer for THIS request:

{dimensions}

Then write 3-5 questions. Good ones:
- change the answer materially — if both answers lead to the same output, cut it
- ask what you cannot look up: her situation, taste, and intent. Never ask her
  something you could research yourself
- are concrete and answerable in a few words, not open-ended essays
- are ordered by how much they change the outcome

Call save_project_questions with them, send them to her as ONE numbered text,
and say she can answer partially or tell you to use your best judgement."""


# Broad families. Naples is an instance of place_setup and the NYC evening is an
# instance of service_sourcing — they are examples of a style of ask, not the
# only two shapes Cord will ever see.
PLAYBOOKS: dict[str, dict] = {
    "place_setup": {
        "label": "Outfitting or preparing a place",
        "expert_framing": (
            "You are an experienced relocation and property-outfitting adviser. Someone "
            "who has set up dozens of second homes knows the answer turns on climate, how "
            "the place will actually be used, and what is worth shipping versus buying "
            "locally — not on a generic checklist of household goods."
        ),
        "intake_questions": [
            "How big is the place, and what kind is it — beachfront, condo, in town, on a course?",
            "What's already there? Furnished, partly furnished, or empty?",
            "What will you mostly be doing there — hosting people, quiet time, going out, or a mix?",
            "Which months will you actually be in residence, and who visits?",
            "Anything you'd rather buy there than ship, or vice versa?",
        ],
        "research_checklist": [
            "Climate through the year, month by month — heat, humidity, rainy season, storm season",
            "What that climate ruins or requires: mould, damp, sun damage, storm preparation",
            "Local dress norms and the social calendar, including whether there is an in-season",
            "What is expensive or hard to get locally versus cheap and easy",
            "Anything specific to this kind of property in this place",
        ],
        "output_template": (
            "Organised room by room or area by area. For each item say why it is on the "
            "list — the climate or use reason — and mark whether to bring it or buy it "
            "there. Call out anything seasonal or time-sensitive separately."
        ),
    },
    "service_sourcing": {
        "label": "Finding, pricing, and booking a service",
        "expert_framing": (
            "You are a concierge who books these for a living. You know the price hinges on "
            "how the job is structured, not just the headline rate — and that the structure "
            "a vendor quotes is often not the cheapest one for the actual itinerary. Get the "
            "shape of the job right first, then price it."
        ),
        "intake_questions": [
            "What exactly needs to happen, with times and addresses in order?",
            "Who is it for, how many people, and any luggage or access needs?",
            "Are there gaps where the provider waits, or is each leg separate?",
            "Any preference on the provider, vehicle, or level of service?",
            "What's the budget posture — cheapest that works, or best experience?",
        ],
        "research_checklist": [
            "Which providers actually serve this area and this kind of job",
            "How the job is normally priced — by the hour, by the leg, or a flat rate — and "
            "which is cheaper for THIS itinerary",
            "Minimums, wait-time and overtime charges",
            "Whether gratuity, tolls, parking, and fees are included or added",
            "Cancellation window and what a deposit commits her to",
            "Recent reputation, not just the marketing copy",
        ],
        "output_template": (
            "A ranked short list. For each: the provider, the total she would actually pay, "
            "what that includes, what it excludes, the cancellation terms, and a link or "
            "number to book. Say which you would pick and why, in one line."
        ),
        # The kind of insight that separates a useful answer from a searchable one.
        "domain_notes": (
            "For a multi-stop outing with waiting between stops, an hourly 'as directed' "
            "charter is almost always cheaper and more reliable than booking each leg as a "
            "separate transfer — and it keeps the same driver and car all evening. Price "
            "both ways before recommending."
        ),
    },
    "event_planning": {
        "label": "Planning an event or gathering",
        "expert_framing": (
            "You are a producer who has run this kind of event many times. A good plan is "
            "specific — a run of show, real vendors, a budget, and the things that go wrong "
            "if nobody thinks about them in advance."
        ),
        "intake_questions": [
            "What's the occasion, and what would make it a success for you?",
            "How many people, who are they, and when is it?",
            "Where — a venue you have, somewhere to find, or your home?",
            "What's the budget range, and what matters most within it?",
            "Anything already booked or decided?",
        ],
        "research_checklist": [
            "Venues, vendors, or suppliers that fit the size and date, with real prices",
            "Lead times — what has to be booked how far out",
            "Local rules that apply: permits, noise, alcohol, parking",
            "Seasonal or date-specific conflicts in that area",
        ],
        "output_template": (
            "A run of show with times, a vendor list with real prices and contacts, a budget "
            "that adds up, and a short list of what to lock in first and by when."
        ),
    },
    "research_brief": {
        "label": "Researching a question properly",
        "expert_framing": (
            "You are a researcher briefing a principal who will act on what you say. Sourced, "
            "specific, and honest about what you could not confirm."
        ),
        "intake_questions": [
            "What decision will this inform?",
            "What have you already looked at or ruled out?",
            "How deep do you want it — a quick read or the full picture?",
        ],
        "research_checklist": [
            "Primary sources over summaries and listicles",
            "Anything time-sensitive, with the date it was checked",
            "Where credible sources disagree",
        ],
        "output_template": (
            "The answer first, then what it rests on, then what remains uncertain. Every "
            "figure carries its source."
        ),
    },
}


# Rough routing only. A miss costs nothing — the derived-question path handles
# anything unmatched and is the normal case, not the exception.
_MATCH_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("place_setup", (
        "pack", "packing", "furnish", "outfit", "move in", "moving in", "set up the",
        "setting up the", "second home", "new house", "new home", "what do i need for the",
    )),
    ("service_sourcing", (
        "car service", "book a", "booking a", "hire", "find me a", "quote", "cheapest",
        "best price", "price out", "driver", "limo", "caterer", "cleaner", "contractor",
    )),
    ("event_planning", (
        "party", "dinner party", "event", "gala", "fundraiser", "celebration", "reception",
        "comedy night", "host a", "hosting a", "shower", "reunion",
    )),
]


def match(request: str) -> str | None:
    """Best-guess family for a request, or None to derive an interview."""
    low = (request or "").lower()
    for kind, hints in _MATCH_HINTS:
        if any(h in low for h in hints):
            return kind
    return None


def get(kind: str | None) -> dict | None:
    return PLAYBOOKS.get(kind or "")


def kinds() -> list[str]:
    return list(PLAYBOOKS)
