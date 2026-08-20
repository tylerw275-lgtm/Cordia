from typing import Callable

from app.config import settings
from app.tools import calendar_tools, email_tools, family_circle_tools, family_tools, feature_tools, lease_tools, memory_tools

# Always-on tools (owner / Cordia)
_BASE_TOOLS: list[dict] = [
    *memory_tools.TOOL_SCHEMAS,
    *family_tools.TOOL_SCHEMAS,  # now includes get_grandkid_activity_balance, log_grandkid_activity, update_family_member_notes
    *calendar_tools.TOOL_SCHEMAS,
    *family_circle_tools.OWNER_TOOL_SCHEMAS,
    *feature_tools.TOOL_SCHEMAS,
]

_BASE_HANDLERS: dict[str, Callable] = {
    "store_memory": memory_tools.store_memory_handler,
    "recall_memory": memory_tools.recall_memory_handler,
    "get_family_member": family_tools.get_family_member_handler,
    "list_family_members": family_tools.list_family_members_handler,
    "list_family_events": family_tools.list_family_events_handler,
    "get_grandkid_activity_balance": family_tools.get_grandkid_activity_balance_handler,
    "log_grandkid_activity": family_tools.log_grandkid_activity_handler,
    "add_family_member": family_tools.add_family_member_handler,
    "update_family_member_notes": family_tools.update_family_member_notes_handler,
    "schedule_family_event": calendar_tools.schedule_event_handler,
    "list_events_by_location": calendar_tools.list_events_by_location_handler,
    **family_circle_tools.OWNER_EXTRA_HANDLERS,
    **feature_tools.HANDLERS,
}


def get_tool_schemas(role: str = "owner") -> list[dict]:
    if role == "family":
        return list(family_circle_tools.FAMILY_TOOL_SCHEMAS)
    schemas = list(_BASE_TOOLS)
    if settings.enable_flight_search:
        from app.tools import flight_tools
        schemas.extend(flight_tools.TOOL_SCHEMAS)
        schemas.extend(flight_tools.PREFERENCE_TOOL_SCHEMAS)
        if settings.enable_flight_booking:
            schemas.extend(flight_tools.BOOKING_TOOL_SCHEMAS)
    if settings.enable_lease_review:
        schemas.extend(lease_tools.TOOL_SCHEMAS)
    if settings.enable_email:
        schemas.extend(email_tools.OWNER_TOOL_SCHEMAS)
    if settings.enable_outbound:
        from app.tools import contact_tools, outbound_tools
        schemas.extend(contact_tools.TOOL_SCHEMAS)
        schemas.extend(outbound_tools.TOOL_SCHEMAS)
    return schemas


def get_handler(tool_name: str, role: str = "owner") -> Callable | None:
    if role == "family":
        return family_circle_tools.FAMILY_HANDLERS.get(tool_name)
    handlers = dict(_BASE_HANDLERS)
    if settings.enable_flight_search:
        from app.tools import flight_tools
        handlers["search_flights"] = flight_tools.search_flights_handler
        handlers["watch_flight_price"] = flight_tools.watch_price_handler
        handlers["get_travel_preferences"] = flight_tools.get_travel_preferences_handler
        handlers["set_travel_preferences"] = flight_tools.set_travel_preferences_handler
        if settings.enable_flight_booking:
            handlers["get_booking_link"] = flight_tools.get_booking_link_handler
    if settings.enable_lease_review:
        handlers["flag_lease_clauses"] = lease_tools.flag_clauses_handler
    if settings.enable_email:
        handlers.update(email_tools.OWNER_EMAIL_HANDLERS)
    if settings.enable_outbound:
        from app.tools import contact_tools, outbound_tools
        handlers.update(contact_tools.HANDLERS)
        handlers.update(outbound_tools.HANDLERS)
    return handlers.get(tool_name)
