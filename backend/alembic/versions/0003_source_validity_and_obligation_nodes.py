"""add source validity records and generic obligation targets

Revision ID: 0003_src_validity
Revises: 0002_extraction_jobs
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_src_validity"
down_revision = "0002_extraction_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_validities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_source_validities_source_created", "source_validities", ["source_id", "created_at"])
    op.add_column("proof_obligations", sa.Column("blocks_node_type", sa.String(32)))
    op.add_column("proof_obligations", sa.Column("blocks_node_id", postgresql.UUID(as_uuid=True)))
    op.execute("UPDATE proof_obligations SET blocks_node_type = 'statement', blocks_node_id = blocks_statement_id WHERE blocks_statement_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("proof_obligations", "blocks_node_id")
    op.drop_column("proof_obligations", "blocks_node_type")
    op.drop_index("ix_source_validities_source_created")
    op.drop_table("source_validities")
