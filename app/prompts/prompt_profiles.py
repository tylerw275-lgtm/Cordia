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
    # Server-side web tool versions this model family accepts. The 2026-02-09
    # variants add dynamic filtering (results are filtered before they reach the
    # context window); older families only have the basic search and no fetch at
    # all, so web_fetch_version is None there rather than a version that 400s.
    web_search_version: str = "web_search_20250305"
    web_fetch_version: str | None = None


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
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    ("claude-opus-5", PromptProfile(
        family="frontier",
        deep_extras={"output_config": {"effort": "high"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    ("claude-sonnet-5", PromptProfile(
        family="frontier",
        deep_extras={"output_config": {"effort": "high"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    ("claude-opus-4-8", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    ("claude-opus-4-7", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    # 4.6 family (current production model): adaptive thinking supported,
    # instructions followed literally.
    ("claude-sonnet-4-6", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
    ("claude-opus-4-6", PromptProfile(
        family="modern",
        deep_extras={"thinking": {"type": "adaptive"}},
        prompting_notes=_MODERN_NOTES,
        web_search_version="web_search_20260209",
        web_fetch_version="web_fetch_20260209",
    )),
]

_DEFAULT = PromptProfile(family="legacy", prompting_notes=_LEGACY_NOTES)


def get_profile(model_id: str) -> PromptProfile:
    model_id = (model_id or "").lower()
    for prefix, profile in _PROFILES:
        if model_id.startswith(prefix):
            return profile
    return _DEFAULT


# Deep work — whether an ask deserves an expert brief and a bigger token budget
# than a conversational SMS reply — is decided by the one intent table, so the
# budget cannot disagree with the brief that was injected alongside it.
from app.prompts.intent import is_deep_work  # noqa: E402,F401
