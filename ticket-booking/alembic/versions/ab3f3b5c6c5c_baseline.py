"""Historical Alembic baseline.

This revision ID already exists in the deployed database's alembic_version
table. The original migration file is not present in the repository, so this
empty revision restores the migration graph without changing application
tables.

Revision ID: ab3f3b5c6c5c
Revises:
Create Date: 2026-10-04
"""

revision = "ab3f3b5c6c5c"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
