"""family_circle — family member access, inputs, and shareable events

Revision ID: 004
Revises: 003
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Family members can be granted access to the family circle
    op.add_column(
        "family_members",
        sa.Column("has_circle_access", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "family_members",
        sa.Column("circle_consented_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Events can be approved by Cordia for family members to see
    op.add_column(
        "family_events",
        sa.Column("shareable_with_family", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "family_events",
        sa.Column("created_by_member_id", UUID(as_uuid=True), sa.ForeignKey("family_members.id"), nullable=True),
    )

    # Things a family member shares to help Cordia connect with them
    op.create_table(
        "family_inputs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_member_id", UUID(as_uuid=True), sa.ForeignKey("family_members.id"), nullable=False),
        sa.Column("about_member_id", UUID(as_uuid=True), sa.ForeignKey("family_members.id"), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),  # gift_idea | engagement_tip | mac_request
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("surfaced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_family_inputs_surfaced", "family_inputs", ["surfaced"])

    # Asks FROM Cordia TO family (e.g. "source gift ideas for a grandchild").
    # Surfaced to the relevant family member the next time they text in.
    op.create_table(
        "family_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("about_member_id", UUID(as_uuid=True), sa.ForeignKey("family_members.id"), nullable=True),
        sa.Column("audience", sa.String(50), nullable=False, server_default="all"),  # 'all' or a member id string
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("fulfilled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_family_requests_fulfilled", "family_requests", ["fulfilled"])


def downgrade() -> None:
    op.drop_index("ix_family_requests_fulfilled", table_name="family_requests")
    op.drop_table("family_requests")
    op.drop_index("ix_family_inputs_surfaced", table_name="family_inputs")
    op.drop_table("family_inputs")
    op.drop_column("family_events", "created_by_member_id")
    op.drop_column("family_events", "shareable_with_family")
    op.drop_column("family_members", "circle_consented_at")
    op.drop_column("family_members", "has_circle_access")
