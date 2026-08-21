"""loyalty_accounts — encrypted rewards program memberships

Revision ID: 010
Revises: 009
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loyalty_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("program_name", sa.String(120), nullable=False),
        sa.Column("program_type", sa.String(20), nullable=False, server_default="airline"),
        sa.Column("airline_iata_code", sa.String(3), nullable=True),
        sa.Column("account_number_encrypted", sa.Text, nullable=True),
        sa.Column("last_four", sa.String(4), nullable=True),
        sa.Column("status_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_loyalty_program_name", "loyalty_accounts", ["program_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_loyalty_program_name", table_name="loyalty_accounts")
    op.drop_table("loyalty_accounts")
