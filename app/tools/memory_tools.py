from sqlalchemy.ext.asyncio import AsyncSession

from app.services import memory_service

TOOL_SCHEMAS = [
    {
        "name": "store_memory",
        "description": "Persist an important fact, preference, or instruction to Cordia's memory. Use after learning something meaningful about her preferences, family, or past experiences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["preference", "history", "fact", "instruction"],
                    "description": "Type of memory being stored",
                },
                "subject": {
                    "type": "string",
                    "description": "Brief label for this memory (e.g. 'seat preference', 'grandchild Emma interests')",
                },
                "content": {
                    "type": "string",
                    "description": "The full memory content to store",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for retrieval (e.g. ['travel', 'flights', 'emma'])",
                },
            },
            "required": ["category", "subject", "content"],
        },
    },
    {
        "name": "recall_memory",
        "description": "Search Cordia's memory for relevant facts and preferences. Use before responding to any request to check what you already know.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (e.g. 'flight preferences', 'grandkids', 'real estate')",
                },
                "category": {
                    "type": "string",
                    "enum": ["preference", "history", "fact", "instruction"],
                    "description": "Optional: filter by memory category",
                },
            },
            "required": ["query"],
        },
    },
]


async def store_memory_handler(db: AsyncSession, **kwargs) -> dict:
    mem = await memory_service.store_memory(db, **kwargs)
    return {"stored": True, "memory_id": str(mem.id), "subject": mem.subject}


async def recall_memory_handler(db: AsyncSession, **kwargs) -> dict:
    memories = await memory_service.search_memories(db, **kwargs)
    return {
        "memories": [
            {"subject": m.subject, "content": m.content, "category": m.category}
            for m in memories
        ],
        "count": len(memories),
    }
