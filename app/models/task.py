"""Something one person needs to do by a date, and whether they have.

Cord could already ask a relative a question and wait for them to reply. What it
could not do was hold a list across a group and know who was still outstanding —
"everyone needs a valid passport before July" is fourteen separate answers, and
nothing tracked them.

One row per person per task, deliberately. A single row with a list of assignees
cannot record that Elliot renewed his and Theo has not, which is the only
question Cordia is actually going to ask.

Note what is NOT here: any way for Cord to chase the assignee. The family did
not sign up to be nagged by an assistant, and Cord does not message people who
did not just message it. Reminders go to Cordia; the chasing is hers, and when
she wants a specific message sent she asks for it through ask_family_member.
`last_surfaced_at` therefore records when *she* was last told, so a digest does
not repeat the same line every morning.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# open    — not done, still counts as outstanding
# done    — completed
# blocked — they have said they cannot yet, with a reason in notes
# skipped — does not apply to this person after all
STATUSES = ("open", "done", "blocked", "skipped")
OPEN_STATUSES = ("open", "blocked")


class FamilyTask(Base):
    __tablename__ = "family_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL means it is the principal's own task rather than a relative's.
    assignee_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family_members.id", ondelete="CASCADE"), nullable=True
    )
    # Which principal's list this is. The same walls as projects and memories.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("authorized_users.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When Cordia was last told about this one, so a digest does not repeat
    # itself daily. Not a record of contacting the assignee — Cord never does.
    last_surfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
