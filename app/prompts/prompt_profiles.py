"""Model-adaptive prompting profiles.

Different Claude generations want different prompting and request parameters:
newer models (4.6+) follow instructions literally and support adaptive thinking,
while older ones need firmer scaffolding and explicit "think step by step"
nudges. Cord reads `settings.claude_model` and picks the matching profile, so
switching models in config automatically selects the right framing — no prompt
rewrite needed.

Each profile controls:
- max_tokens for normal (SMS-sized) vs deep-work replies
- extra request kwargs (adaptive thinking / effort) safe for that model family
- a short prompting-notes block appended to the system prompt

Grounded in Anthropic's published prompting best practices and migration
guidance (adaptive thinking replaces budget_tokens on 4.6+; never rely on
assistant prefill on the 4.6+ family; soften "CRITICAL/MUST" scaffolding on
literal-instruction models).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptProfile:
    family: str
    max_tokens: int = 2048
    deep_max_tokens: int = 8192
    # Extra kwargs merged into messages.create() — only params this family accepts.
    normal_extras: dict = field(default_factory=dict)
    deep_extras: dict = field(default_factory=dict)
    prompting_notes: str = ""


_MODERN_NOTES = (
    "MODEL ADAPTATION: You follow instructions literally and precisely. Apply "
    "guidance at face value without needing repetition or emphasis. For "
    "open-ended deep work, reason step by step internally before answering."
)

_LEGACY_NOTES = (
    "MODEL ADAPTATION: For any open-ended, creative, or research request, think "
    "step by step before answering: restate the goal, list what you know, break "
    "the task into parts, then produce the deliverable."
)

# Ordered most-specific-first; matched by substring against the model id.
_PROFILES: list[tuple[str, PromptProfile]] = [
    # 4.7 / 4.8 / 5 family: adaptive thinking is the norm; effort supported.
    ("claude-fable-5", PromptProfile(
        family="frontier",
        # Thinking is always on for Fable — never send a thinking param.
        deep_extras={"output_config": {"effort": "high"}},
        prompting_notes=_MODERN_NOTES,
    )),
    ("claude-opus-5", PromptProfile(
        family="frontier",
        deep_extras={"output_config": {"effort": "high"}},
        prompting_notes=_MODERN_NOTES,
    )),
    ("claude-sonnet-5", PromptProfile(
        family="frontier",
        deep_extras={"output_config": {"effort": "high"}},
        prompting_notes=_MODERN_NOTES,
    )),
    ("claude-opus-4-8", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
    )),
    ("claude-opus-4-7", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
    )),
    # 4.6 family (current production model): adaptive thinking supported,
    # instructions followed literally.
    ("claude-sonnet-4-6", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
    )),
    ("claude-opus-4-6", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
    )),
]

_DEFAULT = PromptProfile(family="legacy", prompting_notes=_LEGACY_NOTES)


def get_profile(model_id: str) -> PromptProfile:
    model_id = (model_id or "").lower()
    for prefix, profile in _PROFILES:
        if model_id.startswith(prefix):
            return profile
    return _DEFAULT


# Deep work = open-ended creative, planning, or research asks that deserve an
# expert brief and a bigger token budget than a conversational SMS reply.
_DEEP_WORK_KEYWORDS = (
    "plan", "planning", "organize", "brainstorm", "ideas", "research",
    "party", "event", "comedy", "dinner", "gala", "fundraiser", "host",
    "celebration", "itinerary", "compare", "options", "recommend",
    "write", "draft", "prepare", "help me with", "put together",
)


def is_deep_work(message: str, context_hint: str | None = None) -> bool:
    if context_hint in ("trip_planning", "event_planning"):
        return True
    lower = (message or "").lower()
    return any(kw in lower for kw in _DEEP_WORK_KEYWORDS)
