from app.services.travel_prefs import DEFAULT_PREFS, apply_preferences, parse_duration_minutes

OFFERS = [
    {"price": 450, "stops": 0, "via": [], "duration": "PT6H", "carrier": "DL"},
    {"price": 300, "stops": 1, "via": ["ORD"], "duration": "PT4H30M", "carrier": "UA"},
    {"price": 500, "stops": 0, "via": [], "duration": "PT5H45M", "carrier": "AA"},
    {"price": 420, "stops": 1, "via": ["ATL"], "duration": "PT7H", "carrier": "DL"},
]


def test_parse_duration_minutes():
    assert parse_duration_minutes("PT5H30M") == 330
    assert parse_duration_minutes("PT45M") == 45
    assert parse_duration_minutes("P1DT2H") == 1560
    assert parse_duration_minutes(None) is None
    assert parse_duration_minutes("garbage") is None


def test_nonstop_and_avoid_city_filtering_cheapest_first():
    prefs = {**DEFAULT_PREFS, "priority": "cheapest", "avoid_cities": ["ORD"]}
    matching, tradeoffs = apply_preferences(OFFERS, prefs)
    assert [o["price"] for o in matching] == [450, 500]
    assert all(o["stops"] == 0 for o in matching)


def test_notably_better_excluded_option_surfaces_as_tradeoff():
    prefs = {**DEFAULT_PREFS, "priority": "cheapest", "avoid_cities": ["ORD"]}
    _, tradeoffs = apply_preferences(OFFERS, prefs)
    # The ORD connection is both >=15% cheaper and >=60min faster than the best match
    assert len(tradeoffs) == 1
    assert tradeoffs[0]["via"] == ["ORD"]
    assert "ORD" in tradeoffs[0]["excluded_because"]


def test_fastest_priority_sorts_by_duration():
    prefs = {**DEFAULT_PREFS, "priority": "fastest", "avoid_cities": []}
    matching, _ = apply_preferences(OFFERS, prefs)
    assert matching[0]["duration"] == "PT5H45M"


def test_fallback_when_everything_filtered():
    prefs = {**DEFAULT_PREFS, "avoid_cities": ["ORD"]}
    matching, tradeoffs = apply_preferences([OFFERS[1]], prefs)
    assert len(matching) == 1
    assert "excluded_because" in matching[0]
    assert tradeoffs == []


def test_unremarkable_exclusions_stay_hidden():
    offers = [
        {"price": 300, "stops": 0, "via": [], "duration": "PT5H", "carrier": "DL"},
        # only slightly cheaper and slower — not worth surfacing
        {"price": 290, "stops": 1, "via": ["ATL"], "duration": "PT7H", "carrier": "UA"},
    ]
    matching, tradeoffs = apply_preferences(offers, {**DEFAULT_PREFS, "priority": "cheapest"})
    assert len(matching) == 1
    assert tradeoffs == []
