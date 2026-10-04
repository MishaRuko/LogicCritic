"""Connect verified research to laboratory protocol runs."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0012_experiments"
down_revision = "0011_evaluation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_protocols",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("protocol", JSONB(), nullable=False),
        sa.Column("step_excerpts", JSONB(), nullable=False),
        sa.Column("research_fingerprint", sa.String(64), nullable=False),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "protocol_id",
            sa.Uuid(),
            sa.ForeignKey("experiment_protocols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_experiment_runs_status_created", "experiment_runs", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("experiment_runs")
    op.drop_table("experiment_protocols")
