import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OutboundMessage(Base):
    """A message Cord drafted on Cordia's behalf. Nothing in this table sends
    itself: rows are created as drafts, reviewed/edited in conversation, and
    only sent when Cordia explicitly approves (send_outbound). One row per
    recipient; a shared batch_id groups the drafts from one request."""

    __tablename__ = "outbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # email | sms
    to_name: Mapped[str] = mapped_column(String(150), nullable=False)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)  # email addr or phone
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)  # email only
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="draft")  # draft | sent | skipped | canceled
    # Generated server-side at draft time. send_outbound will not send unless
    # this code appears in one of Cordia's own inbound messages — an approval
    # the model cannot manufacture for itself.
    approval_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
