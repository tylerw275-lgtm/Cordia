from typing import Callable

from app.config import settings
from app.tools import calendar_tools, family_tools, lease_tools, memory_tools

# Always-on tools
_BASE_TOOLS: list[dict] = [
    *memory_tools.TOOL_SCHEMAS,
    *family_tools.TOOL_SCHEMAS,
    *calendar_tools.TOOL_SCHEMAS,
]

_BASE_HANDLERS: dict[str, Callable] = {
    "store_memory": memory_tools.store_memory_handler,
    "recall_memory": memory_tools.recall_memory_handler,
    "get_family_member": family_tools.get_family_member_handler,
    "list_family_members": family_tools.list_family_members_handler,
    "list_family_events": family_tools.list_family_events_handler,
    "schedule_family_event": calendar_tools.schedule_event_handler,
}


def get_tool_schemas() -> list[dict]:
    schemas = list(_BASE_TOOLS)
    if settings.enable_flight_search:
        from app.tools import flight_tools
        schemas.extend(flight_tools.TOOL_SCHEMAS)
    if settings.enable_lease_review:
        schemas.extend(lease_tools.TOOL_SCHEMAS)
    return schemas


def get_handler(tool_name: str) -> Callable | None:
    handlers = dict(_BASE_HANDLERS)
    if settings.enable_flight_search:
        from app.tools import flight_tools
        handlers["search_flights"] = flight_tools.search_flights_handler
        handlers["watch_flight_price"] = flight_tools.watch_price_handler
    if settings.enable_lease_review:
        handlers["flag_lease_clauses"] = lease_tools.flag_clauses_handler
    return handlers.get(tool_name)
