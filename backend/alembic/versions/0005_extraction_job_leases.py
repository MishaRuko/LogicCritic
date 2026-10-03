"""add extraction job leases and retry scheduling

Revision ID: 0005_job_leases
Revises: 0004_workspace_cascade
Create Date: 2026-10-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_job_leases"
down_revision = "0004_workspace_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("extraction_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("extraction_jobs", "heartbeat_at")
    op.drop_column("extraction_jobs", "next_attempt_at")
