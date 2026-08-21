"""outbound_messages.approval_code — an approval the model cannot assert

send_outbound was gated on a boolean the model set itself (approved_by_cordia),
so anything that could reach the tool could also approve it. The code is
generated server-side at draft time and must appear in one of Cordia's own
inbound messages before the batch will send.

Revision ID: 009
Revises: 008
"""
import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbound_messages", sa.Column("approval_code", sa.String(12), nullable=True))


def downgrade() -> None:
    op.drop_column("outbound_messages", "approval_code")
