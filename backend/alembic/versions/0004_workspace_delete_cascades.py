"""allow workspace deletion to cascade through graph associations

Revision ID: 0004_workspace_cascade
Revises: 0003_src_validity
Create Date: 2026-10-03
"""

from alembic import op

revision = "0004_workspace_cascade"
down_revision = "0003_src_validity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("reasoning_steps_conclusion_id_fkey", "reasoning_steps", type_="foreignkey")
    op.create_foreign_key(
        "reasoning_steps_conclusion_id_fkey",
        "reasoning_steps",
        "statements",
        ["conclusion_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "reasoning_premises_statement_id_fkey", "reasoning_premises", type_="foreignkey"
    )
    op.create_foreign_key(
        "reasoning_premises_statement_id_fkey",
        "reasoning_premises",
        "statements",
        ["statement_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "statement_excerpts_excerpt_id_fkey", "statement_excerpts", type_="foreignkey"
    )
    op.create_foreign_key(
        "statement_excerpts_excerpt_id_fkey",
        "statement_excerpts",
        "excerpts",
        ["excerpt_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "statement_excerpts_excerpt_id_fkey", "statement_excerpts", type_="foreignkey"
    )
    op.create_foreign_key(
        "statement_excerpts_excerpt_id_fkey",
        "statement_excerpts",
        "excerpts",
        ["excerpt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "reasoning_premises_statement_id_fkey", "reasoning_premises", type_="foreignkey"
    )
    op.create_foreign_key(
        "reasoning_premises_statement_id_fkey",
        "reasoning_premises",
        "statements",
        ["statement_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("reasoning_steps_conclusion_id_fkey", "reasoning_steps", type_="foreignkey")
    op.create_foreign_key(
        "reasoning_steps_conclusion_id_fkey",
        "reasoning_steps",
        "statements",
        ["conclusion_id"],
        ["id"],
        ondelete="RESTRICT",
    )
