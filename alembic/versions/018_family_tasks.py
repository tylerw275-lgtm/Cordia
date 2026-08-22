"""family_tasks — who still has to do what, and by when

Revision ID: 018
Revises: 017
Create Date: 2026-08-22

Cord could ask a relative a question and wait for a reply. It could not hold a
list across a group: "everyone needs a valid passport before July" is fourteen
separate answers, and nothing tracked which were outstanding.

One row per person per task. A single row with an array of assignees cannot
record that one son has renewed his and another has not, which is the only
question actually worth asking.

There is no column for chasing the assignee. The family did not sign up to be
nagged by an assistant; reminders go to Cordia and the chasing is hers.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "family_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("assignee_member_id", UUID(as_uuid=True),
                  sa.ForeignKey("family_members.id", ondelete="CASCADE"), nullable=True),
        sa.Column("owner_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("authorized_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_surfaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # The digest scans for open tasks that are coming due.
    op.create_index("ix_family_tasks_due", "family_tasks", ["status", "due_on"])
    op.create_index("ix_family_tasks_assignee", "family_tasks", ["assignee_member_id"])


def downgrade() -> None:
    op.drop_index("ix_family_tasks_assignee", table_name="family_tasks")
    op.drop_index("ix_family_tasks_due", table_name="family_tasks")
    op.drop_table("family_tasks")
