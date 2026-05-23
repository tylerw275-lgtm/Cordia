import json
import logging
import uuid
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation, Message
from app.prompts.system_prompt import build_system_prompt
from app.services import memory_service
from app.tools.registry import get_handler, get_tool_schemas

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

CONTEXT_KEYWORDS = {
    "trip_planning": ["flight", "fly", "hotel", "trip", "travel", "airport", "book", "vacation", "thanksgiving", "cruise"],
    "family_coordination": ["family", "gather", "birthday", "anniversary", "schedule", "get together", "grandkids", "reunion"],
    "lease_review": ["lease", "rent", "tenant", "landlord", "clause", "renewal", "property", "contract"],
}


def detect_context(message: str) -> str | None:
    lower = message.lower()
    for context, keywords in CONTEXT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return context
    return None


def _extract_text(content: list) -> str:
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


async def get_or_create_conversation(db: AsyncSession, phone_number: str) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.phone_number == phone_number)
        .where(Conversation.is_active == True)
        .order_by(Conversation.created_at.desc())
    )
    conv = result.scalars().first()
    if not conv:
        conv = Conversation(phone_number=phone_number)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
    return conv


async def _load_history(db: AsyncSession, conversation_id: uuid.UUID, limit: int = 20) -> list[dict]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))
    history = []
    for msg in messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})
        elif msg.role == "tool":
            # Tool result messages are stored as JSON
            try:
                history.append({"role": "user", "content": json.loads(msg.content)})
            except json.JSONDecodeError:
                pass
    return history


async def _persist_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: Any,
) -> None:
    if isinstance(content, str):
        raw = content
    else:
        raw = json.dumps(content)
    msg = Message(conversation_id=conversation_id, role=role, content=raw)
    db.add(msg)
    await db.commit()


async def chat(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_message: str,
    context_hint: str | None = None,
) -> str:
    if context_hint is None:
        context_hint = detect_context(user_message)

    # Load relevant memories proactively
    memories = await memory_service.search_memories(db, query=user_message, limit=5)
    memory_block = ""
    if memories:
        lines = [f"- {m.subject}: {m.content}" for m in memories]
        memory_block = "\nRELEVANT MEMORY:\n" + "\n".join(lines)

    # Build system prompt with optional module context
    system = build_system_prompt(context_hint)
    if memory_block:
        system.append({"type": "text", "text": memory_block})

    # Load conversation history
    history = await _load_history(db, conversation_id)

    # Persist user message
    await _persist_message(db, conversation_id, "user", user_message)

    messages = history + [{"role": "user", "content": user_message}]
    tools = get_tool_schemas()

    max_iterations = 10
    for _ in range(max_iterations):
        response = await _client.messages.create(
            model=settings.claude_model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=1024,
        )

        # Persist assistant response
        assistant_content = [block.model_dump() for block in response.content]
        await _persist_message(db, conversation_id, "assistant", assistant_content)

        if response.stop_reason == "end_turn":
            return _extract_text(response.content)

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    handler = get_handler(block.name)
                    if handler is None:
                        result = {"error": f"Unknown tool: {block.name}"}
                    else:
                        try:
                            result = await handler(db=db, **block.input)
                        except Exception as e:
                            logger.error(f"Tool {block.name} error: {e}")
                            result = {"error": str(e)}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            await _persist_message(db, conversation_id, "tool", tool_results)
        else:
            # Unexpected stop reason — return whatever text we have
            return _extract_text(response.content)

    logger.warning(f"Max tool iterations reached for conversation {conversation_id}")
    return "I hit a snag working on that. Try rephrasing your request."
