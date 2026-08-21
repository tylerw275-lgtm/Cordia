"""authorized_users + access_grants — each principal gets their own walled Cord

Revision ID: 014
Revises: 013
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authorized_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Sender resolution runs on every inbound message, by phone or by email.
    op.create_index("ix_authorized_users_phone", "authorized_users", ["phone"])
    op.create_index("ix_authorized_users_email", "authorized_users", ["email"])

    op.create_table(
        "access_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("grantee_id", UUID(as_uuid=True),
                  sa.ForeignKey("authorized_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("authorized_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scope", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_access_grants_grantee", "access_grants", ["grantee_id"])

    # Ownership on the data that must not leak between principals. Existing rows
    # are Cordia's — she is the only principal until this migration runs — and
    # NULL is read as hers, so nothing she already has becomes unreachable.
    op.add_column("memories", sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True))
    op.add_column("projects", sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_memories_owner", "memories", ["owner_user_id"])
    op.create_index("ix_projects_owner", "projects", ["owner_user_id"])

    # Which principal approved an outbound send.
    op.add_column("outbound_messages", sa.Column("approved_by", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("outbound_messages", "approved_by")
    op.drop_index("ix_projects_owner", table_name="projects")
    op.drop_index("ix_memories_owner", table_name="memories")
    op.drop_column("projects", "owner_user_id")
    op.drop_column("memories", "owner_user_id")
    op.drop_index("ix_access_grants_grantee", table_name="access_grants")
    op.drop_table("access_grants")
    op.drop_index("ix_authorized_users_email", table_name="authorized_users")
    op.drop_index("ix_authorized_users_phone", table_name="authorized_users")
    op.drop_table("authorized_users")
