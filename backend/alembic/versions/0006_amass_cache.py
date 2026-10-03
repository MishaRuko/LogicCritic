"""cache Amass records by canonical id

Revision ID: 0006_amass_cache
Revises: 0005_job_leases
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_amass_cache"
down_revision = "0005_job_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amass_cache",
        sa.Column("core", sa.String(32), primary_key=True),
        sa.Column("amass_id", sa.String(64), primary_key=True),
        sa.Column("record", postgresql.JSONB(), nullable=False),
        sa.Column("includes_fulltext", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("amass_cache")
