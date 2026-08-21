import json
import logging
import uuid
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation, Message
from app.prompts.prompt_profiles import get_profile, is_deep_work
from app.prompts.system_prompt import (
    build_family_system_prompt,
    build_system_prompt,
    build_untrusted_system_prompt,
)
from app.services import family_circle_service, family_service, memory_service
from app.tools.registry import get_handler, get_tool_schemas

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

CONTEXT_KEYWORDS = {
    "trip_planning": ["flight", "fly", "hotel", "trip", "travel", "airport", "book", "vacation", "thanksgiving", "cruise"],
    "event_planning": ["party", "comedy", "event", "gala", "fundraiser", "dinner party", "host", "celebration", "show", "night out"],
    "family_coordination": ["family", "gather", "birthday", "anniversary", "schedule", "get together", "grandkids", "reunion"],
    "lease_review": ["lease", "rent", "tenant", "landlord", "clause", "renewal", "property",
                     "contract", "negotiate", "good deal", "square feet", "cam", "escalation",
                     "guarantee", "sublet", "build-out", "buildout", "tenant improvement"],
}


def detect_context(message: str) -> str | None:
    lower = message.lower()
    for context, keywords in CONTEXT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return context
    return None


_FALLBACK_REPLY = "I'm on it - give me a moment and ask me again if you don't hear back."


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


async def record_assistant_message(db: AsyncSession, phone_number: str, text: str) -> None:
    """Persist a proactively-sent assistant message into the person's conversation,
    so a later reply has the context (used by proactive jobs like birthday prep)."""
    conv = await get_or_create_conversation(db, phone_number)
    await _persist_message(db, conv.id, "assistant", text)


# Block shapes the Messages API accepts back as input. `model_dump()` emits
# optional nulls (citations, cache_control) that we don't want to replay, and
# thinking blocks can't be replayed across turns without their live signature.
_REPLAYABLE_KEYS = {
    "text": ("type", "text"),
    "tool_use": ("type", "id", "name", "input"),
    "tool_result": ("type", "tool_use_id", "content", "is_error"),
}
# Keys without which the block is structurally invalid to the API.
_REQUIRED_KEYS = {
    "text": ("text",),
    "tool_use": ("id", "name", "input"),
    "tool_result": ("tool_use_id",),
}
# tool_use is assistant-only and tool_result user-only. Without this, a user who
# literally texts a JSON array of tool_use blocks poisons their own thread.
_ALLOWED_BY_ROLE = {
    "assistant": {"text", "tool_use"},
    "user": {"text", "tool_result"},
}


def _to_api_block(block: Any, role: str) -> dict | None:
    """Normalize one persisted content block for replay, or drop it."""
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    keys = _REPLAYABLE_KEYS.get(block_type)
    if keys is None:
        # thinking / redacted_thinking / anything new: not replayable from the DB
        return None
    if block_type not in _ALLOWED_BY_ROLE.get(role, set()):
        return None
    if any(block.get(k) is None for k in _REQUIRED_KEYS[block_type]):
        return None
    return {k: block[k] for k in keys if block.get(k) is not None}


def _decode_content(raw: str, role: str) -> str | list[dict]:
    """Persisted content is either plain text or a JSON list of blocks."""
    if not raw.lstrip().startswith("["):
        return raw
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(decoded, list) or not all(isinstance(b, dict) and "type" in b for b in decoded):
        return raw
    blocks = [b for b in (_to_api_block(b, role) for b in decoded) if b]
    return blocks or ""


def _sanitize_history(turns: list[dict]) -> list[dict]:
    """Drop anything the Messages API would reject.

    Replaying a tool_result whose tool_use isn't in the immediately preceding
    assistant turn is a 400, and so is an assistant tool_use with no result
    after it. Both are produced routinely by windowing the transcript, so this
    has to run after the window, not before.
    """
    kept: list[dict] = []
    i = 0
    while i < len(turns):
        turn = turns[i]
        content = turn["content"]
        blocks = content if isinstance(content, list) else []

        if turn["role"] == "assistant" and any(b.get("type") == "tool_use" for b in blocks):
            pending = {b["id"] for b in blocks if b.get("type") == "tool_use"}
            nxt = turns[i + 1] if i + 1 < len(turns) else None
            nxt_blocks = nxt["content"] if nxt and isinstance(nxt["content"], list) else []
            answered = {b.get("tool_use_id") for b in nxt_blocks if b.get("type") == "tool_result"}
            if pending <= answered and answered:
                kept.append(turn)
                kept.append({"role": "user", "content": [b for b in nxt_blocks
                                                         if b.get("tool_use_id") in pending]})
                i += 2
                continue
            # Unanswered tool call — keep only the assistant's prose, drop the
            # dangling results turn with it.
            prose = [b for b in blocks if b.get("type") == "text"]
            if prose:
                kept.append({"role": turn["role"], "content": prose})
            i += 2 if answered else 1
            continue

        # A tool_result turn we didn't consume above is orphaned by definition.
        if blocks and any(b.get("type") == "tool_result" for b in blocks):
            i += 1
            continue

        if content:
            kept.append(turn)
        i += 1

    # The API requires the first turn to be a plain user turn — a leading
    # tool_result is just as invalid as a leading assistant turn, and popping
    # only the assistant would expose the tool_result it was paired with.
    while kept:
        first = kept[0]
        blocks = first["content"] if isinstance(first["content"], list) else []
        if first["role"] == "user" and not any(b.get("type") == "tool_result" for b in blocks):
            break
        kept.pop(0)
    return kept


def _trim_to_turns(turns: list[dict], max_turns: int) -> list[dict]:
    """Keep the most recent turns, cutting only at a plain user turn so a
    tool_use/tool_result pair is never split across the boundary."""
    if len(turns) <= max_turns:
        return turns
    # Prefer a cut near max_turns, but fall back to any earlier valid boundary
    # rather than discarding the conversation entirely.
    for start in list(range(len(turns) - max_turns, len(turns))) + list(range(len(turns) - max_turns)):
        turn = turns[start]
        if turn["role"] != "user":
            continue
        content = turn["content"]
        blocks = content if isinstance(content, list) else []
        if not any(b.get("type") == "tool_result" for b in blocks):
            return turns[start:]
    return []


async def _load_history(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int = 40, max_turns: int = 20
) -> list[dict]:
    """Rebuild the conversation as a valid Messages API transcript.

    Rows are fetched generously and sanitized afterwards, because the window
    boundary itself is what orphans tool calls — a tool exchange costs several
    rows, so a raw 20-row window routinely sliced one in half.
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))
    turns: list[dict] = []
    for msg in messages:
        if msg.role in ("user", "assistant"):
            turns.append({"role": msg.role, "content": _decode_content(msg.content, msg.role)})
        elif msg.role == "tool":
            # Tool results are persisted as JSON and replayed as a user turn.
            decoded = _decode_content(msg.content, "user")
            if isinstance(decoded, list) and decoded:
                turns.append({"role": "user", "content": decoded})
    return _trim_to_turns(_sanitize_history(turns), max_turns)


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


async def _build_owner_system(db: AsyncSession, user_message: str, context_hint: str | None, channel: str = "sms") -> list[dict]:
    if context_hint is None:
        context_hint = detect_context(user_message)

    # feature_request memories are an internal team backlog written by
    # request_feature — never part of what Cord recalls for Cordia.
    memories = await memory_service.search_memories(
        db, query=user_message, limit=5, exclude_categories=["feature_request"]
    )
    roster = await family_service.get_family_roster_text(db)
    system = build_system_prompt(context_hint, family_roster=roster, channel=channel)
    if memories:
        lines = [f"- {m.subject}: {m.content}" for m in memories]
        system.append({"type": "text", "text": "\nRELEVANT MEMORY:\n" + "\n".join(lines)})

    # Make Cordia aware of new family contributions without auto-consuming them
    pending = await family_circle_service.get_unsurfaced_inputs(db)
    if pending:
        system.append({
            "type": "text",
            "text": (
                f"\nFAMILY CIRCLE: {len(pending)} new item(s) shared by family that Cordia hasn't seen "
                "(gift ideas, tips, or requests to talk). If relevant to her message, call "
                "get_family_circle_updates to retrieve and mention them warmly."
            ),
        })
    return system


async def _build_family_system(db: AsyncSession, member) -> list[dict]:
    open_reqs = await family_circle_service.get_open_requests_for(db, member)
    req_text = ""
    if open_reqs:
        lines = [f"- {r.prompt}" for r in open_reqs]
        req_text = (
            "Cordia has asked the family for the following — work it into the conversation "
            "naturally and record what they share:\n" + "\n".join(lines)
        )
    return build_family_system_prompt(member.name, req_text)


async def chat(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_message: str,
    context_hint: str | None = None,
    sender_role: str = "owner",
    sender_member: Any = None,
    images: list[dict] | None = None,
    channel: str = "sms",
) -> str:
    if context_hint is None and sender_role != "family":
        context_hint = detect_context(user_message)

    if sender_role == "untrusted":
        # Third-party content: no roster, no memory, minimal tools.
        system = build_untrusted_system_prompt()
    elif sender_role == "family" and sender_member is not None:
        system = await _build_family_system(db, sender_member)
    else:
        system = await _build_owner_system(db, user_message, context_hint, channel=channel)

    # Model-adaptive request shaping: bigger budget + thinking/effort for deep work
    profile = get_profile(settings.claude_model)
    deep = sender_role not in ("family", "untrusted") and is_deep_work(user_message, context_hint)
    max_tokens = profile.deep_max_tokens if deep else profile.max_tokens
    request_extras = dict(profile.deep_extras if deep else profile.normal_extras)
    if profile.prompting_notes:
        system.append({"type": "text", "text": "\n" + profile.prompting_notes})

    # Load conversation history
    history = await _load_history(db, conversation_id)

    # Build this turn's content. Images (from MMS) ride alongside any caption.
    if images:
        caption = user_message.strip() or "(photo sent with no caption)"
        user_content: Any = [*images, {"type": "text", "text": caption}]
        persisted = f"[sent {len(images)} image(s)] {user_message}".strip()
    else:
        user_content = user_message
        persisted = user_message

    # Persist a lightweight text record (we don't store raw image bytes in history)
    await _persist_message(db, conversation_id, "user", persisted)

    messages = history + [{"role": "user", "content": user_content}]
    tools = get_tool_schemas(sender_role)

    max_iterations = 10
    for _ in range(max_iterations):
        response = await _client.messages.create(
            model=settings.claude_model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            **request_extras,
        )

        # Persist assistant response
        assistant_content = [block.model_dump() for block in response.content]
        await _persist_message(db, conversation_id, "assistant", assistant_content)

        if response.stop_reason == "end_turn":
            return _extract_text(response.content) or _FALLBACK_REPLY

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    handler = get_handler(block.name, sender_role)
                    if handler is None:
                        result = {"error": f"Unknown tool: {block.name}"}
                    else:
                        try:
                            if sender_role == "family":
                                result = await handler(db=db, acting_member=sender_member, **block.input)
                            else:
                                result = await handler(db=db, **block.input)
                        except Exception as e:
                            logger.error(f"Tool {block.name} error: {e}")
                            result = {"error": str(e)}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            # Raw blocks on purpose: within a single turn, thinking blocks must
            # be sent back with their signatures intact. _load_history strips
            # them when replaying a *past* turn, where they're invalid.
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            await _persist_message(db, conversation_id, "tool", tool_results)
        else:
            # Unexpected stop reason — return whatever text we have
            return _extract_text(response.content) or _FALLBACK_REPLY

    logger.warning(f"Max tool iterations reached for conversation {conversation_id}")
    return "I hit a snag working on that. Try rephrasing your request."
