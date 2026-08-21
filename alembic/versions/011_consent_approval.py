"""Approval gate on consent — signing the public form no longer grants access

Revision ID: 011
Revises: 010
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sms_consent ADD COLUMN IF NOT EXISTS approval_status "
               "VARCHAR(10) NOT NULL DEFAULT 'pending'")
    op.execute("ALTER TABLE sms_consent ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ")

    # Existing consents from people already on file keep working — this change
    # must not silently cut off anyone Cord is mid-conversation with. Numbers
    # nobody recognises drop to pending, which is the whole point.
    op.execute("""
        UPDATE sms_consent SET approval_status = 'approved', reviewed_at = now()
        WHERE right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) IN (
            SELECT right(regexp_replace(phone, '[^0-9]', '', 'g'), 10)
            FROM family_members WHERE phone IS NOT NULL
            UNION
            SELECT right(regexp_replace(phone, '[^0-9]', '', 'g'), 10)
            FROM contacts WHERE phone IS NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sms_consent DROP COLUMN IF EXISTS approval_status")
    op.execute("ALTER TABLE sms_consent DROP COLUMN IF EXISTS reviewed_at")
