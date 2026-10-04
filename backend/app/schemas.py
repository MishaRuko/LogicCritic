import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class WorkspaceResponse(APIModel):
    id: uuid.UUID
    title: str
    created_at: datetime


class SourceResponse(APIModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    title: str | None
    origin: str
    mime_type: str
    original_filename: str
    content_hash: str
    external_ids: dict
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime


class ExcerptResponse(APIModel):
    id: uuid.UUID
    source_id: uuid.UUID
    text: str
    locator: dict
    sequence: int
    created_at: datetime


class SourceWithExcerptsResponse(SourceResponse):
    excerpts: list[ExcerptResponse]


NodeKind = Literal["statement", "reasoning_step"]
Lifecycle = Literal["proposed"]
AssertionMode = Literal["asserted", "hypothesis", "conditional", "question", "reported"]
StatementRole = Literal["premise", "conclusion", "assumption", "objection", "definition"]
StatementSalience = Literal["core", "secondary", "supporting"]


class ProvenanceInput(BaseModel):
    actor_type: Literal["user", "agent", "extractor", "rule_engine", "integration"]
    actor_id: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    prompt_version: str | None = Field(default=None, max_length=255)
    run_id: str | None = Field(default=None, max_length=255)


class StatementCreateOperation(BaseModel):
    op: Literal["create_statement"]
    client_ref: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    assertion_mode: AssertionMode
    role: StatementRole | None = None
    salience: StatementSalience = "core"
    excerpt_ids: list[uuid.UUID] = Field(default_factory=list)
    provenance: ProvenanceInput
    lifecycle: Lifecycle = "proposed"


class ReasoningStepCreateOperation(BaseModel):
    op: Literal["create_reasoning_step"]
    client_ref: str = Field(min_length=1, max_length=128)
    premise_ids: list[str | uuid.UUID] = Field(default_factory=list)
    conclusion_id: str | uuid.UUID
    explanation: str = Field(min_length=1)
    provenance: ProvenanceInput
    lifecycle: Lifecycle = "proposed"


class RelationCreateOperation(BaseModel):
    op: Literal["create_relation"]
    source_node_kind: NodeKind
    source_node_id: str | uuid.UUID
    relation: Literal["supports", "rebuts", "undercuts", "qualifies", "specializes", "revises"]
    target_node_kind: NodeKind
    target_node_id: str | uuid.UUID
    metadata: dict = Field(default_factory=dict)


class AnnotationCreateOperation(BaseModel):
    op: Literal["create_annotation"]
    subject_type: NodeKind
    subject_id: str | uuid.UUID
    type: str = Field(min_length=1, max_length=128)
    value: dict
    provenance: ProvenanceInput
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["proposed"] = "proposed"


PatchOperation = Annotated[
    StatementCreateOperation
    | ReasoningStepCreateOperation
    | RelationCreateOperation
    | AnnotationCreateOperation,
    Field(discriminator="op"),
]


class GraphPatchRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    operations: list[PatchOperation] = Field(min_length=1)


class GraphPatchResponse(BaseModel):
    patch_id: uuid.UUID
    id_map: dict[str, uuid.UUID]
    accepted_event_ids: list[uuid.UUID]
    affected_node_ids: list[uuid.UUID]


class StatementResponse(APIModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    text: str
    assertion_mode: str
    role: str | None
    salience: str
    lifecycle: str
    provenance: dict
    superseded_by: uuid.UUID | None = None
    created_at: datetime
    excerpt_ids: list[uuid.UUID] = Field(default_factory=list)


class ReasoningStepResponse(APIModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    conclusion_id: uuid.UUID
    premise_ids: list[uuid.UUID] = Field(default_factory=list)
    explanation: str
    lifecycle: str
    provenance: dict
    created_at: datetime


class GraphEdgeResponse(APIModel):
    id: uuid.UUID
    source_node_kind: str
    source_node_id: uuid.UUID
    relation: str
    target_node_kind: str
    target_node_id: uuid.UUID
    metadata: dict = Field(validation_alias="metadata_")


class GraphResponse(BaseModel):
    statements: list[StatementResponse]
    reasoning_steps: list[ReasoningStepResponse]
    relations: list[GraphEdgeResponse]


class ObligationResponse(APIModel):
    id: uuid.UUID
    kind: str
    description: str
    required_condition: str
    blocks_statement_id: uuid.UUID | None
    blocks_node_type: str | None
    blocks_node_id: uuid.UUID | None
    generated_by_rule: str | None
    status: str
    created_at: datetime


class IssueResponse(APIModel):
    id: uuid.UUID
    rule_code: str
    node_type: str
    node_id: uuid.UUID
    details: dict
    status: str
    created_at: datetime


class GraphContextResponse(BaseModel):
    focus_statement: StatementResponse
    upstream_statements: list[StatementResponse]
    downstream_statements: list[StatementResponse]
    reasoning_steps: list[ReasoningStepResponse]
    obligations: list[ObligationResponse]
    issues: list[IssueResponse]


class VerificationResponse(BaseModel):
    verification_event_id: uuid.UUID
    rules_run: list[str]
    issues_opened: int
    issues_resolved: int
    obligations_opened: int
    obligations_resolved: int


class ArgumentCheckRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class ArgumentCheckAssessment(BaseModel):
    reasoning_step_id: uuid.UUID
    verdict: Literal["supported", "needs_support"]
    rationale: str = Field(min_length=1)


class ArgumentCheckOutput(BaseModel):
    assessments: list[ArgumentCheckAssessment]


class ArgumentCheckResponse(BaseModel):
    event_id: uuid.UUID
    checked_steps: int
    flagged_steps: int


class SynthesisRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class SynthesisResponse(BaseModel):
    event_id: uuid.UUID
    candidates_considered: int
    proposed_links: int
    audited_links: int
    links_needing_review: int


class SourceExtractionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class ExtractedStatement(BaseModel):
    client_ref: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$",
        description="A short label you choose for this statement (such as s1), unique here.",
    )
    text: str = Field(min_length=1)
    assertion_mode: AssertionMode
    role: StatementRole | None = None
    salience: StatementSalience = Field(
        description="core for the paper's minimum central contribution; secondary for consequential but noncentral results, limitations, or implications; supporting for direct evidence or design premises."
    )
    supports_ref: str | None = Field(
        description="For a supporting statement: the client_ref of the core or secondary "
        "statement in this result that it is direct evidence or a design premise for. A "
        "supporting statement that supports nothing is not worth extracting. Null otherwise."
    )
    excerpt_ids: list[uuid.UUID] = Field(
        min_length=1,
        description="IDs of the supplied source excerpts that contain this statement.",
    )


class ExtractedReasoningStep(BaseModel):
    client_ref: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$",
        description="A short label you choose for this step, such as r1.",
    )
    premise_refs: list[str] = Field(
        min_length=1,
        description="client_ref values of statements in this same result. Never excerpt IDs.",
    )
    conclusion_ref: str = Field(
        description="The client_ref of a statement in this same result. Never an excerpt ID."
    )
    explanation: str = Field(min_length=1)


class ExtractionOutput(BaseModel):
    statements: list[ExtractedStatement] = Field(default_factory=list)
    reasoning_steps: list[ExtractedReasoningStep] = Field(default_factory=list)


class SourceExtractionResponse(BaseModel):
    model: str
    patch: GraphPatchResponse


class ExtractionJobResponse(APIModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    source_id: uuid.UUID
    model: str
    status: str
    attempts: int
    total_chunks: int
    completed_chunks: int
    output_patch_ids: list[str]
    error: str | None
    next_attempt_at: datetime | None
    heartbeat_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class SourceValidityRequest(BaseModel):
    status: Literal["valid", "invalidated"]
    reason: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    provenance: ProvenanceInput


class SourceValidityResponse(APIModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    reason: str
    provenance: dict
    created_at: datetime


class ReviewDecisionRequest(BaseModel):
    node_type: NodeKind
    node_id: uuid.UUID
    decision: Literal["accepted", "rejected"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    provenance: ProvenanceInput


class ReviewDecisionResponse(BaseModel):
    event_id: uuid.UUID
    node_type: NodeKind
    node_id: uuid.UUID
    lifecycle: Literal["accepted", "rejected"]


DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class AmassSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=10, ge=1, le=25)
    min_publication_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    max_publication_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    min_citation_count: int | None = Field(default=None, ge=0)
    is_retracted: bool | None = None


class AmassSearchResult(BaseModel):
    amass_id: str
    pmid: str | None
    pmcid: str | None
    doi: str | None
    url: str | None
    title: str | None
    abstract_preview: str | None
    authors: list[str]
    journal: str | None
    publication_date: str | None
    citation_count: float | None
    is_retracted: bool | None
    has_fulltext: bool | None


class AmassSearchResponse(BaseModel):
    results: list[AmassSearchResult]


class AmassImportRequest(BaseModel):
    """Identify one BiomedCore record by exactly one of its Amass ID, PMID or DOI."""

    amass_id: str | None = Field(default=None, pattern=r"^AMBC_\w+$")
    pmid: str | None = Field(default=None, pattern=r"^\d{1,10}$")
    doi: str | None = Field(default=None, min_length=5, max_length=255)
    include_fulltext: bool = True

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> "AmassImportRequest":
        given = [value for value in (self.amass_id, self.pmid, self.doi) if value]
        if len(given) != 1:
            raise ValueError("Provide exactly one of amass_id, pmid or doi")
        return self


class AmassImportResponse(BaseModel):
    source: SourceWithExcerptsResponse
    already_imported: bool
    retracted: bool


class AmassRefreshResponse(BaseModel):
    retracted: bool
    newly_invalidated: bool


class AgentRunCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    question: str = Field(
        min_length=5, max_length=2000, description="The question, claim or hypothesis to assess."
    )
    kind: Literal["question", "claim", "hypothesis"] = "question"
    completion_criteria: list[str] = Field(default_factory=list, max_length=8)
    falsifiers: list[str] = Field(default_factory=list, max_length=8)
    mode: Literal["guarded", "baseline"] = "guarded"
    model: str | None = Field(default=None, max_length=255)
    max_turns: int | None = Field(default=None, ge=1, le=60)
    max_web_searches: int | None = Field(default=None, ge=0, le=25)


class AgentGoalResponse(APIModel):
    id: uuid.UUID
    question: str
    kind: str
    completion_criteria: list[str]
    falsifiers: list[str]
    status: str


class AgentCriterionVerdict(BaseModel):
    index: int
    criterion: str
    met: bool
    rationale: str
    supporting_statement_ids: list[str]


class AgentDesignVerdict(BaseModel):
    statement_id: str
    design_shown: bool
    rationale: str


class AgentVerdictResponse(BaseModel):
    """One independent review of the run's evidence against its completion criteria."""

    id: uuid.UUID
    created_at: datetime
    criteria: list[AgentCriterionVerdict]
    designs: list[AgentDesignVerdict]
    searches: list[str]


class AgentRunResponse(APIModel):
    goal: AgentGoalResponse | None = None
    id: uuid.UUID
    workspace_id: uuid.UUID
    goal_id: uuid.UUID
    mode: str
    model: str
    status: str
    budgets: dict
    usage: dict
    error: str | None
    final_report: str | None
    final_statement_id: uuid.UUID | None
    certainty: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AgentEventResponse(APIModel):
    seq: int
    type: str
    payload: dict
    created_at: datetime
