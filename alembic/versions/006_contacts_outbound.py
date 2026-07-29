"""contacts + outbound_messages — Cord's address book and draft/approve queue

Revision ID: 006
Revises: 005
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("whatsapp", sa.String(20), nullable=True),
        sa.Column("relationship", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("tags", ARRAY(sa.String), nullable=True),
        sa.Column("family_member_id", UUID(as_uuid=True), sa.ForeignKey("family_members.id"), nullable=True),
        sa.Column("trusted_inbound", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "outbound_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", sa.String(40), nullable=False, index=True),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("to_name", sa.String(150), nullable=False),
        sa.Column("to_address", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(15), nullable=False, server_default="draft"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("outbound_messages")
    op.drop_table("contacts")
