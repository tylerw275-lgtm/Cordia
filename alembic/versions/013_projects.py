"""projects — multi-turn work that survives the gaps between texts

Revision ID: 013
Revises: 012
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False, server_default="research_brief"),
        sa.Column("status", sa.String(20), nullable=False, server_default="intake"),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("request", sa.Text(), nullable=True),
        sa.Column("brief", JSONB(), nullable=True),
        sa.Column("findings", JSONB(), nullable=True),
        sa.Column("quotes", JSONB(), nullable=True),
        sa.Column("deliverable", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # "What's still open?" is the common read, and per-person once there are
    # several principals.
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_requested_by", "projects", ["requested_by"])


def downgrade() -> None:
    op.drop_index("ix_projects_requested_by", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_table("projects")
