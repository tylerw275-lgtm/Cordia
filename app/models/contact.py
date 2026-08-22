import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Contact(Base):
    """Cord's address book — anyone Cordia may want to reach (family and beyond:
    property managers, friends, trip guests). Contact details are sensitive and
    are never revealed in conversation or message bodies; they are used only as
    sending addresses."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Group labels, e.g. ['st-thomas-2026', 'naples-house']
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    # If this contact is also a FamilyMember, link the profile
    family_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=True
    )
    # Cordia has approved processing inbound email from this address (capture
    # schedules/info as an FYI). Off by default.
    trusted_inbound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When Cordia last invited this person to join the SMS circle (sign the
    # consent form). Consent itself lives in sms_consent, keyed by phone.
    sms_invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
