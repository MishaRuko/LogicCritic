"""judge verdicts, kept out of the run's usage

Revision ID: 0010_judge_verdicts
Revises: 0009_goal_kind
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_judge_verdicts"
down_revision = "0009_goal_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "judge_verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("material", postgresql.JSONB(), nullable=False),
        sa.Column("verdict", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("run_id", "input_hash", name="uq_judge_verdicts_run_input"),
    )
    # Verdicts used to be cached inside the run's usage, which the API returns.
    op.execute("UPDATE agent_runs SET usage = usage - 'judge_cache' WHERE usage ? 'judge_cache'")


def downgrade() -> None:
    op.drop_table("judge_verdicts")
