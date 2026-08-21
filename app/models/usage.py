import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageEvent(Base):
    """One billable thing that happened, with what it cost at the time.

    The cost is stored on the row rather than derived at read time on purpose:
    rates change, and a report of what last month actually cost should not
    silently rewrite itself when a price is updated in config.
    """

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # sms_out | sms_in | email_out | email_in | ai_turn | web_search | web_fetch
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Who it is attributable to — a phone number, an email address, or a name.
    # Free-form because a usage log should never fail to record something just
    # because the actor is not on a roster.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Billable units: SMS segments, emails, searches, or AI turns.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # AI turns only.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 6 decimal places: a single SMS segment costs less than a cent, and
    # rounding to cents per row would floor most of the ledger to zero.
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
