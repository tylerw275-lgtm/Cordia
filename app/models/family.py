import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as sa_relationship

from app.database import Base


class FamilyMember(Base):
    __tablename__ = "family_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    relationship: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male | female | other
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)  # former names
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    anniversary: Mapped[date | None] = mapped_column(Date, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    school_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    grade_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    interests: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    personality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    loyalty_programs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # Family circle: whether this member may text the assistant and contribute
    has_circle_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    circle_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    memories: Mapped[list["Memory"]] = sa_relationship(back_populates="family_member")  # noqa: F821


class FamilyEvent(Base):
    __tablename__ = "family_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    recurrence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    family_member_ids: Mapped[list[str] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Family circle: Cordia must approve an event before family members can see it
    shareable_with_family: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FamilyInput(Base):
    """Something a family member shares to help Cordia connect with them."""
    __tablename__ = "family_inputs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=False)
    about_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # gift_idea | engagement_tip | mac_request
    content: Mapped[str] = mapped_column(Text, nullable=False)
    surfaced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FamilyRequest(Base):
    """An ask from Cordia to family (e.g. 'source gift ideas for a grandchild')."""
    __tablename__ = "family_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    about_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("family_members.id"), nullable=True)
    audience: Mapped[str] = mapped_column(String(50), nullable=False, default="all")  # 'all' or a member id string
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    fulfilled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GrandkidActivity(Base):
    __tablename__ = "grandkid_activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # concert|travel|theme_park|sports|cultural|shopping|restaurant
    participant_ids: Mapped[list[str] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
