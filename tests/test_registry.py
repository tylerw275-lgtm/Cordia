"""The tool registry has to be internally consistent for every flag combination.

Anthropic rejects a request whose tool names are not unique — a 400 that kills
the entire turn, not just the duplicated tool. That is exactly what took Cord
down: `list_sms_roster` is lifted into the always-on set so Cordia can ask who
she may text before outbound is enabled, and `contact_tools` contributes it
again once the flag is on. Nobody saw it because the two halves are in different
files and the combination only exists at runtime.

The matrix is small enough to check exhaustively, so check it exhaustively.
"""
import itertools
from collections import Counter

import pytest

from app.config import settings
from app.tools.registry import get_handler, get_tool_schemas

_FLAGS = (
    "enable_flight_search", "enable_flight_booking", "enable_lease_review",
    "enable_email", "enable_outbound",
)
_ROLES = ("owner", "family", "untrusted")


@pytest.fixture
def flags(mocker):
    def _set(**values):
        for name in _FLAGS:
            mocker.patch.object(settings, name, values.get(name, False))
    return _set


@pytest.mark.parametrize("role", _ROLES)
@pytest.mark.parametrize("combo", list(itertools.product([False, True], repeat=len(_FLAGS))))
def test_tool_names_are_unique_for_every_flag_combination(role, combo, flags):
    """The bug in production, generalised: 2^5 combinations x 3 roles."""
    flags(**dict(zip(_FLAGS, combo)))

    names = [t["name"] for t in get_tool_schemas(role)]
    duplicates = sorted(n for n, count in Counter(names).items() if count > 1)

    assert duplicates == [], (
        f"role={role} flags={dict(zip(_FLAGS, combo))} would be rejected by the "
        f"API with 'Tool names must be unique': {duplicates}"
    )


@pytest.mark.parametrize("role", _ROLES)
def test_every_registered_tool_has_a_handler(role, flags):
    """A schema with no handler is worse than a missing tool: the model calls it,
    gets 'Unknown tool', and has no way to recover."""
    flags(**{name: True for name in _FLAGS})

    missing = [t["name"] for t in get_tool_schemas(role) if get_handler(t["name"], role) is None]
    assert missing == [], f"role={role} offers tools with no handler: {missing}"


@pytest.mark.parametrize("role", _ROLES)
def test_every_tool_has_the_shape_the_api_requires(role, flags):
    flags(**{name: True for name in _FLAGS})

    for schema in get_tool_schemas(role):
        assert schema.get("name"), f"a {role} tool has no name"
        assert schema.get("description"), f"{schema['name']} has no description"
        assert schema.get("input_schema", {}).get("type") == "object", (
            f"{schema['name']} needs an object input_schema"
        )


def test_the_roster_tool_survives_deduping(flags):
    """It is the one deliberately-overlapping tool, so it must still be there
    whichever side contributed it — with outbound off *and* on."""
    for outbound in (False, True):
        flags(enable_outbound=outbound)
        names = {t["name"] for t in get_tool_schemas("owner")}
        assert "list_sms_roster" in names, f"lost with enable_outbound={outbound}"
        assert get_handler("list_sms_roster", "owner") is not None


def test_turning_a_flag_on_only_ever_adds_tools(flags):
    """A flag that removes a capability would be a surprise; this pins the
    direction so the dedupe cannot silently drop something."""
    flags()
    baseline = {t["name"] for t in get_tool_schemas("owner")}

    for flag in _FLAGS:
        flags(**{flag: True})
        with_flag = {t["name"] for t in get_tool_schemas("owner")}
        assert baseline <= with_flag, f"{flag}=True removed {baseline - with_flag}"


@pytest.mark.parametrize("role", ("family", "untrusted"))
def test_restricted_roles_never_widen_with_flags(role, flags):
    """Feature flags are about Cordia's capabilities. They must not quietly hand
    the family circle — or attacker-controlled content — anything new."""
    flags()
    off = {t["name"] for t in get_tool_schemas(role)}
    flags(**{name: True for name in _FLAGS})
    on = {t["name"] for t in get_tool_schemas(role)}

    assert off == on, f"{role} gained {on - off} when flags were switched on"
