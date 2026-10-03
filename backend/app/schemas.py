import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    relation: Literal["rebuts", "undercuts", "qualifies", "specializes", "revises"]
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
    lifecycle: str
    provenance: dict
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


class SourceExtractionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)


class ExtractedStatement(BaseModel):
    client_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
    text: str = Field(min_length=1)
    assertion_mode: AssertionMode
    role: StatementRole | None = None
    excerpt_ids: list[uuid.UUID] = Field(min_length=1)


class ExtractedReasoningStep(BaseModel):
    client_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
    premise_refs: list[str] = Field(min_length=1)
    conclusion_ref: str
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
