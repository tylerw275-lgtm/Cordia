import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthorizedUser(Base):
    """Someone who gets their own Cord.

    Not a family-circle member — those have a deliberately restricted assistant.
    A principal gets the full thing, in their own workspace. Cordia, her husband
    Tom, and her assistant Karie are each principals, and none of them sees
    another's work unless it has been shared.
    """

    __tablename__ = "authorized_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The account holder. Exactly one principal is the owner: proactive jobs go
    # only to her, and only she can share her own data.
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AccessGrant(Base):
    """One deliberate act of sharing.

    The default is that nothing crosses between principals. A grant is how
    Cordia opens a specific door — a single project, or a named area like her
    loyalty accounts — and it is revocable. Briefings are deliberately NOT
    grants: telling Karie about something once must not subscribe her to it.
    """

    __tablename__ = "access_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grantee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("authorized_users.id", ondelete="CASCADE"), nullable=False
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("authorized_users.id", ondelete="SET NULL"), nullable=True
    )
    # A named area (loyalty, travel_prefs, leases, family_notes, projects) or
    # the literal word "project" paired with a resource_id.
    scope: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Revoked rather than deleted: "who could see this, and when" is a question
    # worth being able to answer later.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
