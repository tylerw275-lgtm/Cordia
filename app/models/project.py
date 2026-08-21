import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    """A piece of work that spans more than one message.

    SMS is a lossy, slow channel: Cordia may answer three of five intake
    questions on Tuesday and the rest on Thursday, from a different phone, after
    a dozen unrelated messages. Conversation history alone cannot be trusted to
    carry that — so the interview, the answers, the research and the deliverable
    live here, where they survive the gap and can be read back.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # A playbook family, or "derived" when Cord designed the interview itself.
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="research_brief")
    # intake | researching | delivered | closed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="intake", index=True)
    # Who asked. Free-form (phone or email) to match the conversation key.
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # The original one-liner, kept verbatim — the answers only make sense against it.
    request: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The interview: [{"question": ..., "answer": ... | None}]. Questions are
    # stored even before they are answered, so a half-finished intake is
    # resumable rather than re-asked from scratch.
    brief: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    findings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Priced options, each carrying the source it was read from.
    quotes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    deliverable: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
