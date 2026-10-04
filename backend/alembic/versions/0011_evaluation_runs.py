"""persist blind evaluation runs

Revision ID: 0011_evaluation_runs
Revises: 0010_judge_verdicts
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_evaluation_runs"
down_revision = "0010_judge_verdicts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evaluation_run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("packet", postgresql.JSONB(), nullable=False),
        sa.Column("packet_hash", sa.String(64), nullable=False),
        sa.Column("rubric", postgresql.JSONB(), nullable=False),
        sa.Column("blind_labels", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evaluation_cases_run", "evaluation_cases", ["evaluation_run_id", "position"])
    op.create_table(
        "evaluation_outputs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evaluation_case_id", sa.Uuid(), sa.ForeignKey("evaluation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("arm", sa.String(16), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evaluation_case_id", "arm", name="uq_evaluation_outputs_case_arm"),
    )
    op.create_table(
        "evaluation_judgments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evaluation_case_id", sa.Uuid(), sa.ForeignKey("evaluation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("material", postgresql.JSONB(), nullable=False),
        sa.Column("verdict", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evaluation_case_id", name="uq_evaluation_judgments_case"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_judgments")
    op.drop_table("evaluation_outputs")
    op.drop_index("ix_evaluation_cases_run", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_runs")
