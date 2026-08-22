"""Drop the users table, conversations.user_id, and contacts.whatsapp

Revision ID: 016
Revises: 015
Create Date: 2026-08-22

`users` predates the assistant having a real identity model. Nothing has ever
written a row to it: principals live in `authorized_users`, family in
`family_members`, and everyone else in `contacts`. `conversations.user_id` is
the foreign key into it and has never been set on any row, so the relationship
it supports resolves to NULL for every conversation in the database.

`contacts.whatsapp` is a channel Cord cannot send on and no code reads.

Deliberately kept: `contacts.family_member_id`. It is also unread today, but it
is the cross-link between the address book and the family roster - the spine
that stops `add_contact` creating a second Bea. Dropping it would mean adding it
back the moment that gets built.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The FK has to go before the table it points at.
    op.drop_column("conversations", "user_id")
    op.drop_table("users")
    op.drop_column("contacts", "whatsapp")


def downgrade() -> None:
    op.add_column("contacts", sa.Column("whatsapp", sa.String(20), nullable=True))
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(20), unique=True, nullable=True),
        sa.Column("email", sa.String(255), unique=True, nullable=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("role", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.add_column(
        "conversations",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
