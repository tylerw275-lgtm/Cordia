"""Every registered tool, called the way the dispatcher actually calls it.

This is the test that was missing. `claude_service.chat()` passed `acting_user`
into every owner handler; handlers all take `**kw` so it bound cleanly, and then
several of them forward `**kwargs` verbatim into strictly-typed functions. Those
raised TypeError, the loop's `except` turned it into `{"error": ...}` for the
model, and memory, family creation, calendar capture and flight search were dead
for six deploys with nothing visible anywhere.

No existing test called a tool handler through the dispatcher. The one test that
exercised the tool_use branch patched `get_handler` to return None.

Inputs are generated from each tool's own `input_schema` rather than a
hand-written table. A new tool is covered the moment it is registered, and the
arguments used here are by construction the ones the model is told to send —
a hand-maintained table drifts from the schema and then tests the wrong call.

Wrong *values* are fine and expected: a tool complaining that a lease id does not
exist is doing its job. A tool that cannot be *invoked* is not.
"""
import inspect
import uuid

import pytest

from app.config import settings
from app.tools.registry import get_handler, get_tool_schemas

_ALL_FLAGS = (
    "enable_flight_search", "enable_flight_booking", "enable_lease_review",
    "enable_email", "enable_outbound", "enable_family_coordination",
)

# Values by field name, so generated input is plausible enough to reach the
# handler body rather than dying in a date parse.
_BY_NAME = {
    "origin": "BNA", "destination": "MCO",
    "email": "probe@example.com", "phone": "+15550001111",
    "program_type": "airline", "program_name": "Delta",
    "category": "outing", "priority": "cheapest",
    "what": "loyalty", "decision": "approved",
}
_BY_SUFFIX = {
    "_date": "2026-09-01", "_on": "2026-09-01", "_id": str(uuid.uuid4()),
}
_BY_TYPE = {"string": "probe", "integer": 1, "number": 1, "boolean": False,
            "array": [], "object": {}}


def _value(field: str, spec: dict):
    # The schema's own constraints first: an enum names its legal values, and a
    # description saying YYYY-MM-DD is a date field whatever it is called
    # (save_lease wants lease_start, which no suffix rule would catch).
    if spec.get("enum"):
        return spec["enum"][0]
    if "YYYY-MM-DD" in (spec.get("description") or ""):
        return "2026-09-01"
    if field in _BY_NAME:
        return _BY_NAME[field]
    for suffix, val in _BY_SUFFIX.items():
        if field.endswith(suffix):
            return val
    return _BY_TYPE.get(spec.get("type", "string"), "probe")


def _sample_input(schema: dict) -> dict:
    body = schema.get("input_schema", {})
    props = body.get("properties", {})
    return {name: _value(name, props.get(name, {})) for name in body.get("required", [])}


def _tools(role: str) -> list[dict]:
    for flag in _ALL_FLAGS:
        setattr(settings, flag, True)
    return get_tool_schemas(role)


_OWNER_TOOLS = {t["name"]: t for t in _tools("owner")}
_FAMILY_TOOLS = {t["name"]: t for t in _tools("family")}


@pytest.fixture(autouse=True)
def _isolated(mocker):
    """Widest tool surface, and every outbound side effect stays in the test."""
    for flag in _ALL_FLAGS:
        mocker.patch.object(settings, flag, True)
    mocker.patch("app.services.sms_service.send_sms", new=mocker.AsyncMock(return_value=True))
    mocker.patch("app.services.email_service.send_email",
                 new=mocker.AsyncMock(return_value={"sent": True}))


@pytest.mark.parametrize("tool_name", sorted(_OWNER_TOOLS))
@pytest.mark.asyncio
async def test_every_owner_tool_is_callable_through_the_dispatcher(db, tool_name):
    handler = get_handler(tool_name, "owner")
    assert handler is not None, f"{tool_name} is registered with no handler"

    # Exactly what claude_service.chat() does for a principal.
    extra = {"acting_user": None} if getattr(handler, "wants_actor", False) else {}

    try:
        result = await handler(db=db, **extra, **_sample_input(_OWNER_TOOLS[tool_name]))
    except TypeError as e:
        pytest.fail(
            f"{tool_name} cannot be invoked by the dispatcher: {e}. This is the "
            "failure that killed memory for six deploys — a handler forwarding "
            "**kwargs into a strictly-typed function."
        )
    except Exception as e:
        pytest.fail(f"{tool_name} raised {type(e).__name__}: {e}")

    assert isinstance(result, dict), f"{tool_name} returned {type(result).__name__}, not a dict"


@pytest.mark.parametrize("tool_name", sorted(_FAMILY_TOOLS))
@pytest.mark.asyncio
async def test_every_family_tool_is_callable_through_the_dispatcher(db, tool_name):
    """The family path passes `acting_member` instead — a separate call shape,
    so it needs its own coverage."""
    from app.models.family import FamilyMember

    member = FamilyMember(name="Probe Relative", relationship="child", has_circle_access=True)
    db.add(member)
    await db.commit()

    handler = get_handler(tool_name, "family")
    assert handler is not None, f"{tool_name} is registered with no handler"

    try:
        result = await handler(
            db=db, acting_member=member, **_sample_input(_FAMILY_TOOLS[tool_name])
        )
    except Exception as e:
        pytest.fail(f"{tool_name} raised {type(e).__name__}: {e}")

    assert isinstance(result, dict)


# --- the rule that makes the original bug unrepeatable ----------------------

@pytest.mark.asyncio
async def test_a_handler_that_did_not_opt_in_never_sees_actor_context(db):
    from app.tools import memory_tools

    assert getattr(get_handler("store_memory", "owner"), "wants_actor", False) is False

    # And it genuinely cannot survive being handed one — which is why the
    # dispatcher must not hand it out uninvited.
    with pytest.raises(TypeError):
        await memory_tools.store_memory_handler(
            db=db, acting_user=object(), category="fact", subject="x", content="y"
        )


@pytest.mark.parametrize("tool_name", [
    "start_project", "get_project", "list_projects", "save_project_answers", "send_outbound",
])
def test_handlers_that_scope_by_principal_declare_it(tool_name):
    """These read per-principal data. Losing the marker would silently stop the
    workspace walls being enforced rather than raising anything."""
    assert getattr(get_handler(tool_name, "owner"), "wants_actor", False), (
        f"{tool_name} lost its actor context"
    )


def test_every_owner_tool_accepts_db_as_a_keyword():
    """Handlers are dispatched with db=; a positional-only declaration would
    fail at dispatch rather than at import."""
    for name in _OWNER_TOOLS:
        params = inspect.signature(get_handler(name, "owner")).parameters
        assert "db" in params, f"{name} has no db parameter"
        assert params["db"].kind is not inspect.Parameter.POSITIONAL_ONLY, name


def test_generated_input_covers_every_required_field():
    """If this drifts, the tests above would call tools with missing arguments
    and 'pass' for the wrong reason."""
    for name, schema in _OWNER_TOOLS.items():
        required = set(schema.get("input_schema", {}).get("required", []))
        assert set(_sample_input(schema)) == required, name
