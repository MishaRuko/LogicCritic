"""research goals, agent runs and their event traces

Revision ID: 0008_research_agent
Revises: 0007_statement_salience
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_research_agent"
down_revision = "0007_statement_salience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_goals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("completion_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("falsifiers", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.Uuid(),
            sa.ForeignKey("research_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("budgets", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("final_report", sa.Text()),
        sa.Column("final_statement_id", sa.Uuid()),
        sa.Column("certainty", sa.String(16)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_agent_runs_workspace_key"),
    )
    op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"])
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_events_run_seq"),
    )


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_index("ix_agent_runs_status_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("research_goals")
