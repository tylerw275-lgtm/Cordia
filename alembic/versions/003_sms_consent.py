"""sms_consent — track opt-in/out records per phone number

Revision ID: 003
Revises: 002
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms_consent",
        sa.Column("phone", sa.String(20), primary_key=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(50), nullable=False),  # inbound_text | keyword_start
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sms_consent")
