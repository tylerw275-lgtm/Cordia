"""Condensing a conversation once it is a week old.

History is a window. A trip planned across a year outlives any window however
wide, so past a point replaying every message costs more than it is worth and
still loses the beginning. Old messages stop being replayed and are represented
by a summary instead.

Two rules shape everything here, and both are about what happens when this goes
wrong rather than when it works.

**Nothing is deleted.** `summary_through` is a watermark saying how far the
summary reaches, not a delete marker. Every message row stays. A summary that
lost something can be rebuilt from the rows; deleted history cannot.

**The watermark only moves after a summary is stored.** If the model call fails,
the conversation is untouched and the next run tries again. A half-summarised
conversation must never be reachable — that would silently drop the very
messages this exists to preserve.

The judgment about what to keep is the whole feature, so it is spelled out
below rather than left to "summarise this". A bad summary means Cord confidently
remembers a condensed version of something that did not happen, which is worse
than forgetting.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)

_PROMPT = """You are condensing an assistant's conversation with someone so it can
be recalled later. This replaces the raw messages, which will no longer be visible
to you or to anyone reading back — so what you leave out is gone from recall.

KEEP:
- Decisions, and what was chosen over what
- Commitments: who owes what, by when, and to whom
- Facts about people: preferences, constraints, relationships, health, taste
- Open threads, and what each one is waiting on
- Specifics exactly as given — names, dates, prices, numbers, addresses,
  confirmation codes. Never round a figure or paraphrase a name.

DROP:
- Greetings, thanks, acknowledgements, "sounds good"
- The assistant's holding notes ("working on it", "one moment")
- Anything the assistant said about itself or its own capabilities
- Plans that were superseded — except one line saying what replaced them

RULES:
- Never invent. If something is unclear, write what was actually said.
- When unsure whether a detail matters, KEEP it.
- Write compact notes, not prose. This is read to reconstruct context, not for
  pleasure. No preamble, no "here is a summary", no closing remarks.
- Preserve chronology where it matters and collapse it where it does not.
"""


def _render(messages: list[Message]) -> str:
    """The messages as a readable transcript for the summariser."""
    lines = []
    for msg in messages:
        if msg.role == "tool":
            continue                       # tool plumbing is not conversation
        content = msg.content or ""
        if content.lstrip().startswith("["):
            # An assistant turn is stored as JSON blocks; keep only its words.
            try:
                blocks = json.loads(content)
                content = " ".join(
                    b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            except (ValueError, TypeError):
                pass
        content = content.strip()
        if content:
            who = "Them" if msg.role == "user" else "Assistant"
            lines.append(f"{who}: {content}")
    return "\n".join(lines)


def cutoff(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=settings.history_summary_after_days)


async def _pending(db: AsyncSession, conversation: Conversation,
                   now: datetime | None = None) -> list[Message]:
    """Messages old enough to condense that the watermark does not cover."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .where(Message.created_at <= cutoff(now))
        .order_by(Message.created_at)
    )
    if conversation.summary_through is not None:
        stmt = stmt.where(Message.created_at > conversation.summary_through)
    return list((await db.execute(stmt)).scalars())


async def due(db: AsyncSession, now: datetime | None = None) -> list[Conversation]:
    """Conversations with enough old, uncovered messages to be worth a call."""
    threshold = settings.history_summary_min_messages
    rows = (await db.execute(select(Conversation))).scalars().all()
    out = []
    for conversation in rows:
        count = (await db.execute(
            select(func.count(Message.id))
            .where(Message.conversation_id == conversation.id)
            .where(Message.created_at <= cutoff(now))
            .where(
                Message.created_at > conversation.summary_through
                if conversation.summary_through is not None
                else Message.created_at.isnot(None)
            )
        )).scalar() or 0
        if count >= threshold:
            out.append(conversation)
    return out


async def summarise(db: AsyncSession, conversation: Conversation,
                    now: datetime | None = None) -> bool:
    """Condense everything older than the cutoff. Returns whether it ran.

    Never raises: this runs unattended, and a conversation that cannot be
    summarised should simply keep replaying its messages.
    """
    from app.services import claude_service

    try:
        pending = await _pending(db, conversation, now)
        if len(pending) < settings.history_summary_min_messages:
            return False

        transcript = _render(pending)
        if not transcript.strip():
            # Nothing but tool plumbing. Advance past it rather than looking at
            # it again every night.
            conversation.summary_through = pending[-1].created_at
            await db.commit()
            return False

        prior = (f"\n\nEVERYTHING BEFORE THIS, already condensed:\n{conversation.summary}"
                 if conversation.summary else "")
        response = await claude_service._client.messages.create(
            model=settings.claude_model,
            max_tokens=2048,
            system=_PROMPT,
            messages=[{"role": "user", "content":
                       f"NEW MESSAGES TO CONDENSE:\n{transcript}{prior}"}],
        )
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        if not text:
            logger.warning(f"Empty summary for conversation {conversation.id}; leaving history")
            return False

        # Billed like every other model call, so a nightly job cannot quietly
        # become a line item nobody can see.
        try:
            from app.services import usage_service
            await usage_service.record(
                db, "ai_turn", actor=conversation.phone_number,
                model=settings.claude_model, usage=getattr(response, "usage", None),
                cost_usd=usage_service.ai_turn_cost(
                    settings.claude_model, getattr(response, "usage", None))
                if getattr(response, "usage", None) else 0.0,
            )
        except Exception as e:                   # pragma: no cover - defensive
            logger.warning(f"Could not bill a summary for {conversation.id}: {e}")

        conversation.summary = text[:settings.history_summary_max_chars]
        conversation.summarised_at = datetime.now(timezone.utc)
        # Last, and only now: the watermark is what stops these messages being
        # replayed, so it must never move ahead of a stored summary.
        conversation.summary_through = pending[-1].created_at
        await db.commit()
        logger.info(
            f"Condensed {len(pending)} messages for conversation {conversation.id}"
        )
        return True
    except Exception as e:
        logger.error(f"Could not summarise conversation {conversation.id}: {e}")
        # Leave the watermark where it was: the messages keep replaying, which
        # is the safe failure. The rollback is scoped and followed by a refresh
        # because rolling back expires the object, and a caller that then reads
        # conversation.summary_through would trigger IO from wherever it stands.
        try:
            await db.rollback()
            await db.refresh(conversation)
        except Exception:                        # pragma: no cover - defensive
            pass
        return False


def for_prompt(conversation: Conversation) -> str:
    """The block naming what came before, or "" when there is nothing."""
    if not conversation or not conversation.summary:
        return ""
    return (
        "\nWHAT CAME BEFORE (condensed from earlier in this conversation; the "
        "messages themselves are no longer shown):\n"
        f"{conversation.summary}\n"
        "Treat this as things that were actually said. If she asks about "
        "something here, answer from it rather than saying you do not recall."
    )
