"""The lease flow used to dead-end: flag_lease_clauses demanded a lease_id that
Cord had no way to obtain, so a lease texted in was read and then lost."""
import pytest

from app.tools import lease_tools
from app.tools.registry import get_handler, get_tool_schemas


def test_cord_can_create_a_lease_not_just_annotate_one():
    names = [t["name"] for t in get_tool_schemas("owner")]
    assert "save_lease" in names
    assert get_handler("save_lease", "owner") is not None


def test_lease_tools_are_owner_only():
    for name in lease_tools.HANDLERS:
        assert get_handler(name, "untrusted") is None
        assert get_handler(name, "family") is None


def test_every_schema_has_a_handler():
    declared = {t["name"] for t in lease_tools.TOOL_SCHEMAS}
    assert declared == set(lease_tools.HANDLERS), "schema/handler mismatch"


def test_save_lease_requires_the_dates_reminders_depend_on():
    schema = next(t for t in lease_tools.TOOL_SCHEMAS if t["name"] == "save_lease")
    required = schema["input_schema"]["required"]
    assert "lease_end" in required and "property_address" in required
