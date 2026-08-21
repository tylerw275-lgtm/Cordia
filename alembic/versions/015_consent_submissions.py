"""consent_submissions — the names people typed on the public consent form

Revision ID: 015
Revises: 014
Create Date: 2026-08-21

The table already exists in production because the form handler creates it
lazily with CREATE TABLE IF NOT EXISTS. That is why the gap went unnoticed:
`list_consent_requests` joins it unguarded, so on any database where nobody had
yet submitted the form — a fresh deploy, a restored backup, a test database —
the tool raised UndefinedTable instead of reporting an empty queue.

This migration is written to be a no-op against the existing production table
(same columns, same types) and to create it everywhere else.
"""
import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("consent_submissions"):
        return
    op.create_table(
        "consent_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Looked up by the last 10 digits when Cordia reviews the approval queue.
    op.create_index("ix_consent_submissions_phone", "consent_submissions", ["phone"])


def downgrade() -> None:
    op.drop_index("ix_consent_submissions_phone", table_name="consent_submissions")
    op.drop_table("consent_submissions")
