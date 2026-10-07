import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperimentProtocol(Base):
    __tablename__ = "experiment_protocols"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    protocol: Mapped[dict] = mapped_column(JSONB)
    step_excerpts: Mapped[dict] = mapped_column(JSONB)
    research_fingerprint: Mapped[str] = mapped_column(String(64))
    extraction_method: Mapped[str] = mapped_column(String(32))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (Index("ix_experiment_runs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    protocol_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_protocols.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    mode: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_sources_workspace_hash"),
        Index("ix_sources_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    origin: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    external_ids: Mapped[dict] = mapped_column(JSONB, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Excerpt(Base):
    __tablename__ = "excerpts"
    __table_args__ = (
        UniqueConstraint("source_id", "sequence", name="uq_excerpts_source_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(JSONB)
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        Index("ix_statements_workspace_lifecycle", "workspace_id", "lifecycle"),
        Index("ix_statements_workspace_salience", "workspace_id", "salience"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    assertion_mode: Mapped[str] = mapped_column(String(32))
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    salience: Mapped[str] = mapped_column(String(32), default="core")
    lifecycle: Mapped[str] = mapped_column(String(32), default="proposed")
    provenance: Mapped[dict] = mapped_column(JSONB)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReasoningStep(Base):
    __tablename__ = "reasoning_steps"
    __table_args__ = (Index("ix_reasoning_steps_workspace_lifecycle", "workspace_id", "lifecycle"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    conclusion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE")
    )
    explanation: Mapped[str] = mapped_column(Text)
    lifecycle: Mapped[str] = mapped_column(String(32), default="proposed")
    provenance: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReasoningPremise(Base):
    __tablename__ = "reasoning_premises"

    reasoning_step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reasoning_steps.id", ondelete="CASCADE"), primary_key=True
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)


class StatementExcerpt(Base):
    __tablename__ = "statement_excerpts"

    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True
    )
    excerpt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("excerpts.id", ondelete="CASCADE"), primary_key=True
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (Index("ix_graph_edges_workspace_source", "workspace_id", "source_node_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_node_kind: Mapped[str] = mapped_column(String(32))
    source_node_id: Mapped[uuid.UUID] = mapped_column()
    relation: Mapped[str] = mapped_column(String(32))
    target_node_kind: Mapped[str] = mapped_column(String(32))
    target_node_id: Mapped[uuid.UUID] = mapped_column()
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (Index("ix_annotations_subject", "subject_type", "subject_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column()
    type: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphEvent(Base):
    __tablename__ = "graph_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_events_workspace_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_extraction_jobs_workspace_key"
        ),
        Index("ix_extraction_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    output_patch_ids: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceValidity(Base):
    __tablename__ = "source_validities"
    __table_args__ = (Index("ix_source_validities_source_created", "source_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    provenance: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProofObligation(Base):
    __tablename__ = "proof_obligations"
    __table_args__ = (Index("ix_obligations_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    required_condition: Mapped[str] = mapped_column(Text)
    blocks_statement_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    blocks_node_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocks_node_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    generated_by_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (Index("ix_issues_workspace_status", "workspace_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    rule_code: Mapped[str] = mapped_column(String(128))
    node_type: Mapped[str] = mapped_column(String(32))
    node_id: Mapped[uuid.UUID] = mapped_column()
    details: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AmassCacheEntry(Base):
    """A fetched Amass record, kept by canonical Amass ID so repeat imports cost no API credits."""

    __tablename__ = "amass_cache"

    core: Mapped[str] = mapped_column(String(32), primary_key=True)
    amass_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    record: Mapped[dict] = mapped_column(JSONB)
    includes_fulltext: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchGoal(Base):
    """The question an agent run investigates, and what would count as answering it."""

    __tablename__ = "research_goals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="question", server_default="question")
    completion_criteria: Mapped[list] = mapped_column(JSONB, default=list)
    falsifiers: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_agent_runs_workspace_key"),
        Index("ix_agent_runs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    goal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_goals.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(16))  # "guarded" or "baseline"
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    budgets: Mapped[dict] = mapped_column(JSONB, default=dict)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_statement_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    certainty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JudgeVerdict(Base):
    """What the judge was shown and what it decided, so a check is auditable and never repeated.

    One row per distinct input in a run: asking again about the same evidence reuses the row.
    """

    __tablename__ = "judge_verdicts"
    __table_args__ = (UniqueConstraint("run_id", "input_hash", name="uq_judge_verdicts_run_input"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    input_hash: Mapped[str] = mapped_column(String(64))
    material: Mapped[dict] = mapped_column(JSONB)
    verdict: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentEvent(Base):
    """One step of a run, numbered in order. The trace the replay view and evaluation read."""

    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_events_run_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRun(Base):
    """A persisted, paired baseline-versus-guarded experiment."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (Index("ix_evaluation_cases_run", "evaluation_run_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    packet: Mapped[dict] = mapped_column(JSONB)
    packet_hash: Mapped[str] = mapped_column(String(64))
    rubric: Mapped[dict] = mapped_column(JSONB, default=dict)
    blind_labels: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationOutput(Base):
    __tablename__ = "evaluation_outputs"
    __table_args__ = (
        UniqueConstraint("evaluation_case_id", "arm", name="uq_evaluation_outputs_case_arm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE")
    )
    arm: Mapped[str] = mapped_column(String(16))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT")
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationJudgment(Base):
    __tablename__ = "evaluation_judgments"
    __table_args__ = (UniqueConstraint("evaluation_case_id", name="uq_evaluation_judgments_case"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE")
    )
    model: Mapped[str] = mapped_column(String(255))
    material: Mapped[dict] = mapped_column(JSONB)
    verdict: Mapped[dict] = mapped_column(JSONB)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationScore(Base):
    """An absolute, single-answer score: no other answer is shown, so no position or length
    comparison can enter it."""

    __tablename__ = "evaluation_scores"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_output_id", "scorer", name="uq_evaluation_scores_output_scorer"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_output_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_outputs.id", ondelete="CASCADE")
    )
    scorer: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(255))
    material: Mapped[dict] = mapped_column(JSONB)
    score: Mapped[dict] = mapped_column(JSONB)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperimentRecord(Base):
    """A content-addressed report linked to its durable experiment run."""

    __tablename__ = "experiment_records"
    __table_args__ = (Index("ix_experiment_records_run", "run_id", "created_at"),)

    record_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id", ondelete="CASCADE"))
    record: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
