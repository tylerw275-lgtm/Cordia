"""One turn at a time per conversation, whatever channel it arrived on.

Cordia asked what to pack, clarified "clothes", then added "it's a second home"
— three taps in quick succession. Every inbound webhook started its own turn
immediately, so all three ran in parallel, each loading history before the
others had written to it. She got two answers racing: one still asking the
questions the other had already answered.

This lived in `app/api/sms.py`, which meant it only ever protected SMS. The
email path writes to the *same* conversations — a principal's thread is keyed on
their number, and Cordia's own inbound email keys on her phone — so a text and
an email arriving together reproduced the original bug exactly, in one process,
with the lock sitting right there unused.

Correctness here assumes one worker process. `scripts/start.sh` runs uvicorn
with no `--workers`, and `app/main.py` refuses to start if that is overridden,
because the failure is silent: dedup, serialisation and batching all quietly
degrade rather than erroring.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

INBOX: "dict[str, list[tuple[str, list]]]" = {}
LOCKS: "dict[str, asyncio.Lock]" = {}

MAX_TRACKED_CONVERSATIONS = 500


def lock_for(key: str) -> asyncio.Lock:
    lock = LOCKS.get(key)
    if lock is None:
        lock = LOCKS[key] = asyncio.Lock()
    return lock


def sweep() -> None:
    """Drop locks nobody holds or wants.

    Keyed by conversation and otherwise immortal. Reaching into a lock's private
    waiter list to decide would break on any asyncio change, so a bounded sweep
    of the unlocked, un-queued ones is enough.
    """
    if len(LOCKS) <= MAX_TRACKED_CONVERSATIONS:
        return
    for key, lock in list(LOCKS.items()):
        if not lock.locked() and not INBOX.get(key):
            LOCKS.pop(key, None)


def merge(batch: list) -> tuple[str, list]:
    """Fold several messages into the single one a person actually meant.

    "What to pack though. Clothes" followed by "It's a second home" is one
    thought split across two taps, not two questions. Answering them separately
    produces two replies that talk past each other.
    """
    body = "\n".join(b for b, _ in batch if b and b.strip())
    media = [m for _, ms in batch for m in (ms or [])]
    return body, media


async def run_turn(key: str, body: str, media: list, handler) -> None:
    """Queue a message and take the conversation's turn.

    Whoever holds the lock drains everything waiting, so anything that arrives
    mid-turn joins the next turn rather than starting a competing one.
    Deliberately no early "someone else is working" return: the gap between that
    check and acquiring the lock is exactly where a message goes missing.

    `handler(merged_body, merged_media)` does the actual work and owns its own
    database session — the request-scoped one is closed once the webhook has
    responded.
    """
    INBOX.setdefault(key, []).append((body, media))
    async with lock_for(key):
        try:
            while True:
                batch = INBOX.pop(key, [])
                if not batch:
                    break
                merged_body, merged_media = merge(batch)
                if len(batch) > 1:
                    logger.info(f"Folding {len(batch)} messages from {key} into one turn")
                await handler(merged_body, merged_media)
        except Exception as e:
            logger.error(f"Turn failed for {key}: {e}", exc_info=True)
        finally:
            INBOX.pop(key, None)
    sweep()
