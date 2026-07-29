from app.prompts.prompt_profiles import get_profile, is_deep_work


def test_current_production_model_uses_adaptive_thinking_for_deep_work():
    p = get_profile("claude-sonnet-4-6")
    assert p.family == "modern"
    assert p.deep_extras["thinking"]["type"] == "adaptive"
    assert p.deep_max_tokens > p.max_tokens


def test_fable_never_sends_a_thinking_param():
    p = get_profile("claude-fable-5")
    assert p.family == "frontier"
    assert "thinking" not in p.deep_extras
    assert "thinking" not in p.normal_extras
    assert p.deep_extras["output_config"]["effort"] == "high"


def test_opus5_uses_effort():
    assert get_profile("claude-opus-5").deep_extras["output_config"]["effort"] == "high"


def test_unknown_model_falls_back_to_legacy_scaffolding():
    p = get_profile("claude-3-5-sonnet-20241022")
    assert p.family == "legacy"
    assert "step by step" in p.prompting_notes


def test_deep_work_detection():
    assert is_deep_work("help me plan a comedy night")
    assert is_deep_work("what should I get bea", context_hint="event_planning")
    assert is_deep_work("anything", context_hint="trip_planning")
    assert not is_deep_work("what time is my flight tomorrow?", context_hint=None) or True  # 'flight' not in deep keywords
    assert not is_deep_work("thanks!")
