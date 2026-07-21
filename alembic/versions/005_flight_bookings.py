"""flight_bookings — orders booked via Duffel Links hosted checkout

Revision ID: 005
Revises: 004
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flight_bookings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("duffel_order_id", sa.String(50), nullable=False, unique=True),
        sa.Column("booking_reference", sa.String(20), nullable=True),
        sa.Column("origin", sa.String(10), nullable=True),
        sa.Column("destination", sa.String(10), nullable=True),
        sa.Column("depart_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("currency", sa.String(5), nullable=False, server_default="USD"),
        sa.Column("passengers", ARRAY(sa.String), nullable=True),
        sa.Column("raw_order", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("flight_bookings")
