"""align existing database with current domain models

Revision ID: 20261004_0001
Revises: ab3f3b5c6c5c
Create Date: 2026-10-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261004_0001"
down_revision = "ab3f3b5c6c5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This migration is intended for databases that already have the
    # historical baseline schema. Fresh databases are bootstrapped by the
    # deployment script before Alembic is executed.

    # Users
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=30),
            nullable=False,
            server_default="CUSTOMER",
        ),
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    # Events
    op.add_column(
        "events",
        sa.Column(
            "capacity",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="PUBLISHED",
        ),
    )
    op.create_index("ix_events_status", "events", ["status"], unique=False)

    # Bookings
    op.add_column(
        "bookings",
        sa.Column("reference", sa.String(length=30), nullable=True),
    )
    op.execute(
        "UPDATE bookings "
        "SET reference = 'TKT-' || LPAD(id::text, 10, '0') "
        "WHERE reference IS NULL"
    )
    op.alter_column("bookings", "reference", nullable=False)
    op.create_index(
        "ix_bookings_reference",
        "bookings",
        ["reference"],
        unique=True,
    )

    # Constraints
    op.create_unique_constraint(
        "uq_event_seat_number",
        "seats",
        ["event_id", "seat_number"],
    )
    op.create_unique_constraint(
        "uq_booking_seat",
        "booking_seats",
        ["booking_id", "seat_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_booking_seat",
        "booking_seats",
        type_="unique",
    )
    op.drop_constraint(
        "uq_event_seat_number",
        "seats",
        type_="unique",
    )

    op.drop_index(
        "ix_bookings_reference",
        table_name="bookings",
    )
    op.drop_column("bookings", "reference")

    op.drop_index(
        "ix_events_status",
        table_name="events",
    )
    op.drop_column("events", "status")
    op.drop_column("events", "capacity")

    op.drop_index(
        "ix_users_role",
        table_name="users",
    )
    op.drop_column("users", "role")
