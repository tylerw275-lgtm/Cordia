"""conversations.summary — what a thread amounted to, once it is old enough

Revision ID: 019
Revises: 018
Create Date: 2026-08-22

History is a window, and a trip planned across a year outlives any window
however wide. Once messages are older than a week they stop being replayed and
are represented by a summary instead.

The messages themselves are never deleted. `summary_through` is a watermark
saying how far the summary reaches, not a delete marker - a summary that lost
something can be rebuilt from the rows, and deleted history cannot be.
"""
import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("conversations",
                  sa.Column("summary_through", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations",
                  sa.Column("summarised_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summarised_at")
    op.drop_column("conversations", "summary_through")
    op.drop_column("conversations", "summary")
