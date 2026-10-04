"""record whether a research goal is a question, a claim or a hypothesis

Revision ID: 0009_goal_kind
Revises: 0008_research_agent
Create Date: 2026-10-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_goal_kind"
down_revision = "0008_research_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_goals",
        sa.Column("kind", sa.String(16), nullable=False, server_default="question"),
    )


def downgrade() -> None:
    op.drop_column("research_goals", "kind")
