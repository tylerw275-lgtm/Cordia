"""family equity — add gender, aliases, parent_id, address, nickname + grandkid_activities table

Revision ID: 002
Revises: 001
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("family_members", sa.Column("nickname", sa.String(100), nullable=True))
    op.add_column("family_members", sa.Column("gender", sa.String(10), nullable=True))
    op.add_column("family_members", sa.Column("aliases", postgresql.ARRAY(sa.String), nullable=True))
    op.add_column("family_members", sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("family_members.id"), nullable=True))
    op.add_column("family_members", sa.Column("address", sa.Text, nullable=True))

    op.create_table(
        "grandkid_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("activity_date", sa.Date, nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("participant_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("grandkid_activities")
    op.drop_column("family_members", "address")
    op.drop_column("family_members", "parent_id")
    op.drop_column("family_members", "aliases")
    op.drop_column("family_members", "gender")
    op.drop_column("family_members", "nickname")
