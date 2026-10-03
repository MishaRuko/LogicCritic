"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    uuid_type = postgresql.UUID(as_uuid=True)
    json_type = postgresql.JSONB(astext_type=sa.Text())
    now = sa.text("CURRENT_TIMESTAMP")

    op.create_table(
        "workspaces",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_table(
        "sources",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512)),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("external_ids", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "content_hash", name="uq_sources_workspace_hash"),
    )
    op.create_index("ix_sources_workspace_created", "sources", ["workspace_id", "created_at"])
    op.create_table(
        "excerpts",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("source_id", uuid_type, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", json_type, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("source_id", "sequence", name="uq_excerpts_source_sequence"),
    )
    op.create_table(
        "statements",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("assertion_mode", sa.String(32), nullable=False),
        sa.Column("role", sa.String(32)),
        sa.Column("lifecycle", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("provenance", json_type, nullable=False),
        sa.Column("superseded_by", uuid_type),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_statements_workspace_lifecycle", "statements", ["workspace_id", "lifecycle"])
    op.create_table(
        "reasoning_steps",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conclusion_id", uuid_type, sa.ForeignKey("statements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("lifecycle", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("provenance", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_reasoning_steps_workspace_lifecycle", "reasoning_steps", ["workspace_id", "lifecycle"])
    op.create_table(
        "reasoning_premises",
        sa.Column("reasoning_step_id", uuid_type, sa.ForeignKey("reasoning_steps.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("statement_id", uuid_type, sa.ForeignKey("statements.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
    )
    op.create_table(
        "statement_excerpts",
        sa.Column("statement_id", uuid_type, sa.ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("excerpt_id", uuid_type, sa.ForeignKey("excerpts.id", ondelete="RESTRICT"), primary_key=True),
    )
    op.create_table(
        "graph_edges",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_node_kind", sa.String(32), nullable=False),
        sa.Column("source_node_id", uuid_type, nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("target_node_kind", sa.String(32), nullable=False),
        sa.Column("target_node_id", uuid_type, nullable=False),
        sa.Column("metadata", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_graph_edges_workspace_source", "graph_edges", ["workspace_id", "source_node_id"])
    op.create_table(
        "annotations",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", uuid_type, nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("value", json_type, nullable=False),
        sa.Column("provenance", json_type, nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_annotations_subject", "annotations", ["subject_type", "subject_id"])
    op.create_table(
        "graph_events",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("provenance", json_type, nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_events_workspace_key"),
    )
    op.create_table(
        "proof_obligations",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_condition", sa.Text(), nullable=False),
        sa.Column("blocks_statement_id", uuid_type),
        sa.Column("generated_by_rule", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_obligations_workspace_status", "proof_obligations", ["workspace_id", "status"])
    op.create_table(
        "issues",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("workspace_id", uuid_type, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_code", sa.String(128), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("node_id", uuid_type, nullable=False),
        sa.Column("details", json_type, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_issues_workspace_status", "issues", ["workspace_id", "status"])


def downgrade() -> None:
    for index in [
        "ix_issues_workspace_status",
        "ix_obligations_workspace_status",
        "ix_annotations_subject",
        "ix_graph_edges_workspace_source",
        "ix_reasoning_steps_workspace_lifecycle",
        "ix_statements_workspace_lifecycle",
        "ix_sources_workspace_created",
    ]:
        op.drop_index(index)
    for table in [
        "issues", "proof_obligations", "graph_events", "annotations", "graph_edges",
        "statement_excerpts", "reasoning_premises", "reasoning_steps", "statements",
        "excerpts", "sources", "workspaces",
    ]:
        op.drop_table(table)
