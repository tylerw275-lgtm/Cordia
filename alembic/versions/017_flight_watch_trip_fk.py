"""Add the flight_watches.trip_id foreign key the model already declares

Revision ID: 017
Revises: 016
Create Date: 2026-08-22

FlightWatch.trip_id is declared as a ForeignKey with ondelete="SET NULL", but
migration 001 created the column as a plain UUID with no constraint. So the ORM
believes deleting a trip nulls its watches, and the database does not: it would
leave watches pointing at a trip id that no longer exists, and the price-history
endpoint would return an empty list rather than an error - the kind of wrong
answer nobody investigates.

Any orphan values are cleared first, since the constraint cannot be added while
they exist.
"""
import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE flight_watches SET trip_id = NULL WHERE trip_id IS NOT NULL "
        "AND trip_id NOT IN (SELECT id FROM trips)"
    )
    op.create_foreign_key(
        "fk_flight_watches_trip_id", "flight_watches", "trips",
        ["trip_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_flight_watches_trip_id", "flight_watches", type_="foreignkey")
