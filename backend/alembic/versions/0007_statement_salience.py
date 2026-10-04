"""add statement salience

Revision ID: 0007_statement_salience
Revises: 0006_amass_cache
Create Date: 2026-10-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_statement_salience"
down_revision = "0006_amass_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "statements",
        sa.Column("salience", sa.String(32), nullable=False, server_default="core"),
    )
    op.create_index("ix_statements_workspace_salience", "statements", ["workspace_id", "salience"])


def downgrade() -> None:
    op.drop_index("ix_statements_workspace_salience", table_name="statements")
    op.drop_column("statements", "salience")
