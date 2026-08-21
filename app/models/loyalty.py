import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LoyaltyAccount(Base):
    """A rewards program Cordia belongs to.

    Account numbers are encrypted at rest and never returned to the model or
    shown in conversation — only the program name and last four digits. The
    plaintext leaves this table in exactly one direction: into Duffel, for
    airline programs, so fares and mileage accrual reflect her status.

    Credit-card entries record the *rewards program* (e.g. Amex Membership
    Rewards) so Cord can reason about transfer partners. Card numbers are
    never stored — see loyalty_service.save_account.
    """

    __tablename__ = "loyalty_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # airline | hotel | credit_card
    program_type: Mapped[str] = mapped_column(String(20), nullable=False, default="airline")
    # Duffel needs this for airline programs, e.g. 'DL', 'AA', 'UA'
    airline_iata_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    account_number_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # e.g. "Diamond Medallion", "transfers 1:1 to Delta/Hilton"
    status_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
