"""Nightly: condense conversations that have aged past the window.

Scheduled rather than done lazily when a conversation is next used. Lazy would
avoid work on dormant threads, but it puts a model call in front of a live turn
— and the whole point is that she has just texted and is waiting. Latency on the
reply is the one cost not worth paying to save a few cents.
"""
import logging

from app.database import get_db_session
from app.services import history_summary

logger = logging.getLogger(__name__)


async def condense_old_conversations() -> None:
    async with get_db_session() as db:
        try:
            pending = await history_summary.due(db)
        except Exception as e:
            logger.error(f"Could not list conversations to condense: {e}")
            return
        if not pending:
            return

        logger.info(f"Condensing {len(pending)} conversation(s)")
        done = 0
        for conversation in pending:
            # summarise() leaves the watermark alone on failure and does not
            # raise today — but this runs unattended over every conversation
            # there is, and relying on a distant invariant means one bad row
            # silently ends the night's work for everyone else.
            try:
                if await history_summary.summarise(db, conversation):
                    done += 1
            except Exception as e:
                logger.error(f"Skipped conversation {conversation.id}: {e}")
        logger.info(
            f"Condensed {done} of {len(pending)}; the rest keep replaying in full"
        )
