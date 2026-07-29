"""contacts.sms_invited_at — track who's been invited to join the SMS circle

Revision ID: 007
Revises: 006
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("sms_invited_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "sms_invited_at")
