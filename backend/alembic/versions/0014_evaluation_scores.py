"""persist absolute per-answer evaluation scores

Revision ID: 0014_evaluation_scores
Revises: 0013_experiment_records
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_evaluation_scores"
down_revision = "0013_experiment_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evaluation_output_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_outputs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scorer", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("material", postgresql.JSONB(), nullable=False),
        sa.Column("score", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evaluation_output_id", "scorer", name="uq_evaluation_scores_output_scorer"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_scores")
