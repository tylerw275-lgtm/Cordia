"""conversations.phone_number — widen for email addresses

The column is the conversation key, and the email path uses an email address as
that key when a family member has no phone on file. At String(20) Postgres
raised 22001 and every entry point swallowed it, silently dropping the email.

Revision ID: 008
Revises: 007
"""
import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "conversations",
        "phone_number",
        existing_type=sa.String(20),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "conversations",
        "phone_number",
        existing_type=sa.String(255),
        type_=sa.String(20),
        existing_nullable=False,
    )
