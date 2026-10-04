"""Persist immutable experiment reports for downloads and share links."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0013_experiment_records"
down_revision = "0012_experiments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_records",
        sa.Column("record_hash", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("experiment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_experiment_records_run", "experiment_records", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_table("experiment_records")
