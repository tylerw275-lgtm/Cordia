"""Cordia's family roster and historical grandkid activity log.

The single source of truth for the family data. Imported by
``app.services.family_seed`` (which loads it into the database on boot) and by
``scripts/seed_family.py`` (the manual CLI). Nothing in ``app/`` imports from
``scripts/``, so this lives here rather than there.
"""
from datetime import date

FAMILY = [
    # ── Cordia's sons ───────────────────────────────────────────────────────
    {
        "name": "Aaron Wilkinson",
        "relationship": "son",
        "gender": "male",
        "aliases": ["Brad"],
        "birthday": date(1983, 8, 7),
        "phone": "6158539483",
        "address": "1019 Amelia Park Dr, Franklin, TN 37067",
        "city": "Franklin",
        "state": "TN",
        "personality_notes": "Recently built a pool at the house. Advocated for the boys getting more dedicated time with Cordia.",
    },
    {
        "name": "Ryan Wilkinson",
        "relationship": "son",
        "gender": "male",
        "aliases": ["Hunter"],
        "birthday": date(1981, 9, 29),
        "phone": "6157156648",
        "address": "3089 Oxford Glen Drive, Franklin, TN 37067",
        "city": "Franklin",
        "state": "TN",
    },
    {
        "name": "Tyler Wilkinson",
        "relationship": "son",
        "gender": "male",
        "birthday": date(1985, 7, 27),
        "phone": "6157080002",
        "address": "1026 Cambridge Crescent, Norfolk, VA 23508",
        "city": "Norfolk",
        "state": "VA",
        "personality_notes": "Youngest son. Enjoys cold water and cold plunging — the only family member who does.",
    },

    # ── Daughters-in-law ────────────────────────────────────────────────────
    {
        "name": "Amber Wilkinson",
        "relationship": "daughter-in-law",
        "gender": "female",
        "birthday": date(1980, 9, 7),
        "phone": "16154232821",
        "personality_notes": "Aaron's wife. Mother of Brighton and Bea.",
        "parent": "Aaron Wilkinson",  # used to resolve spouse relationship context
    },
    {
        "name": "Kristen Wilkinson",
        "relationship": "daughter-in-law",
        "gender": "female",
        "birthday": date(1986, 1, 14),
        "phone": "17575814718",
        "address": "1026 Cambridge Crescent, Norfolk, VA 23508",
        "city": "Norfolk",
        "state": "VA",
        "personality_notes": "Tyler's wife. Her parents Dick and Verna live next door at 1030 Cambridge Crescent.",
    },
    {
        "name": "Sarah",
        "relationship": "daughter-in-law",
        "gender": "female",
        "birthday": date(1984, 5, 7),
        "phone": "5164744247",
        "address": "3089 Oxford Glen Drive, Franklin, TN 37067",
        "city": "Franklin",
        "state": "TN",
        "personality_notes": "Ryan's wife. Mother of Merrick.",
    },

    # ── In-laws ─────────────────────────────────────────────────────────────
    {
        "name": "Dick",
        "relationship": "in-law (Kristen's father)",
        "gender": "male",
        "address": "1030 Cambridge Crescent, Norfolk, VA",
        "city": "Norfolk",
        "state": "VA",
    },
    {
        "name": "Verna",
        "relationship": "in-law (Kristen's mother)",
        "gender": "female",
        "address": "1030 Cambridge Crescent, Norfolk, VA",
        "city": "Norfolk",
        "state": "VA",
    },

    # ── Grandsons ───────────────────────────────────────────────────────────
    {
        "name": "Brighton Wilkinson",
        "relationship": "grandson",
        "gender": "male",
        "birthday": date(2018, 3, 13),
        "parent": "Aaron Wilkinson",
        "interests": ["Legos", "video games", "swimming", "being active"],
        "personality_notes": "Loves playing video games, especially with his dad. Big into Legos.",
    },
    {
        "name": "Elijah Wilkinson",
        "relationship": "grandson",
        "gender": "male",
        "birthday": date(2018, 4, 9),
        "parent": "Tyler Wilkinson",
        "interests": ["Legos", "Harry Potter", "reading", "swimming"],
        "personality_notes": "Loves Legos. Deep into Harry Potter right now — re-reading 'Harry Potter and the Cursed Child.'",
    },
    {
        "name": "Merrick Wilkinson",
        "relationship": "grandson",
        "gender": "male",
        "parent": "Ryan Wilkinson",
        "interests": ["swimming"],
    },

    # ── Granddaughters ──────────────────────────────────────────────────────
    {
        "name": "Zoë Wilkinson",
        "relationship": "granddaughter",
        "gender": "female",
        "birthday": date(2012, 9, 18),
        "parent": "Tyler Wilkinson",
        "interests": ["swimming", "concerts"],
    },
    {
        "name": "Annabelle Wilkinson",
        "nickname": "Annie",
        "relationship": "granddaughter",
        "gender": "female",
        "birthday": date(2014, 10, 20),
        "parent": "Tyler Wilkinson",
        "interests": ["swimming"],
    },
    {
        "name": "Joy Wilkinson",
        "relationship": "granddaughter",
        "gender": "female",
        "birthday": date(2020, 9, 8),
        "parent": "Tyler Wilkinson",
        "interests": ["swimming"],
    },
    {
        "name": "Bea Wilkinson",
        "relationship": "granddaughter",
        "gender": "female",
        "birthday": date(2016, 9, 25),
        "email": "wilkinson.bea@icloud.com",
        "parent": "Aaron Wilkinson",
        "interests": [
            "animals", "unique cultural experiences", "concerts",
            "ice cream", "macaroons", "boba tea", "swimming",
            "being pampered", "getting nails done",
        ],
        "personality_notes": (
            "Loves animals and seeing how other people live and talk. "
            "Indecisive when pressed to make a decision — offer her 2 curated options, not open questions. "
            "Would enjoy animal tours. Comfortable being pampered. "
            "Unsure about the Outback of Australia specifically, though open to animal experiences. "
            "Enjoys concerts. Loves ice cream, macaroons, and boba tea."
        ),
    },
]

# ---------------------------------------------------------------------------
# Historical grandkid activity log
# (dates are approximate where noted)
# ---------------------------------------------------------------------------

ACTIVITIES = [
    {
        "title": "Taylor Swift Eras Tour",
        "activity_date": date(2025, 1, 1),  # 2025 per Cordia — approximate
        "category": "concert",
        "participant_names": ["Zoë Wilkinson", "Bea Wilkinson"],
        "notes": (
            "Girls flew private to Indianapolis and back to Nashville. "
            "Box seats — Cordia knew the stadium owner. Premium experience."
        ),
    },
    {
        "title": "Hong Kong Trip",
        "activity_date": date(2025, 11, 1),  # November 2025
        "category": "travel",
        "participant_names": ["Bea Wilkinson", "Annabelle Wilkinson"],
        "notes": "Bea and Annie traveled to Hong Kong with another cousin. Girls trip.",
    },
    {
        "title": "New York City — Wives and Daughters",
        "activity_date": date(2024, 1, 1),  # year before Legoland, approximate
        "category": "travel",
        "participant_names": ["Zoë Wilkinson", "Annabelle Wilkinson", "Joy Wilkinson", "Bea Wilkinson"],
        "notes": (
            "All wives and daughters to NYC for a special event. "
            "Aaron later pointed out that the boys had been left out repeatedly."
        ),
    },
    {
        "title": "Legoland New York",
        "activity_date": date(2024, 6, 1),  # approx 2 years ago from May 2026
        "category": "theme_park",
        "participant_names": ["Brighton Wilkinson", "Elijah Wilkinson", "Merrick Wilkinson"],
        "notes": (
            "First dedicated boys trip after Aaron raised the imbalance. "
            "Initial plan was to recreate the girls NYC trip (Rockettes, Central Park, shopping) — "
            "adjusted to Legoland which was age-appropriate for the boys."
        ),
    },
]
