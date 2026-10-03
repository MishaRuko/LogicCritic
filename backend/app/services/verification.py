import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Annotation,
    GraphEvent,
    Issue,
    ProofObligation,
    ReasoningPremise,
    ReasoningStep,
    Source,
    SourceValidity,
    Statement,
    StatementExcerpt,
)
from app.schemas import VerificationResponse

RULE_CODES = [
    "ungrounded_statement",
    "missing_premise",
    "causality_overclaim",
    "scope_leap",
    "direct_conflict",
    "invalidated_source",
    "reported_limitation",
]

LIMITATION_MARKERS = (
    "limited sample size",
    "risk of overfitting",
    "may overestimate",
    "may constrain",
)


@dataclass(frozen=True)
class Finding:
    rule_code: str
    node_type: str
    node_id: uuid.UUID
    details: dict
    obligation_kind: str
    obligation_description: str
    required_condition: str


async def run_verification(session: AsyncSession, workspace_id: uuid.UUID) -> VerificationResponse:
    statements = list(
        await session.scalars(select(Statement).where(Statement.workspace_id == workspace_id))
    )
    steps = list(
        await session.scalars(
            select(ReasoningStep).where(ReasoningStep.workspace_id == workspace_id)
        )
    )
    annotations = list(
        await session.scalars(select(Annotation).where(Annotation.workspace_id == workspace_id))
    )
    premises = list(
        await session.execute(
            select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id)
            .join(ReasoningStep, ReasoningStep.id == ReasoningPremise.reasoning_step_id)
            .where(ReasoningStep.workspace_id == workspace_id)
        )
    )
    evidence_ids = set(
        await session.scalars(
            select(StatementExcerpt.statement_id)
            .join(Statement, Statement.id == StatementExcerpt.statement_id)
            .where(Statement.workspace_id == workspace_id)
        )
    )

    annotation_map: dict[tuple[str, uuid.UUID], list[Annotation]] = {}
    for annotation in annotations:
        annotation_map.setdefault((annotation.subject_type, annotation.subject_id), []).append(
            annotation
        )
    premise_map: dict[uuid.UUID, list[uuid.UUID]] = {step.id: [] for step in steps}
    for step_id, statement_id in premises:
        premise_map.setdefault(step_id, []).append(statement_id)
    inferred_ids = {step.conclusion_id for step in steps if step.lifecycle != "rejected"}

    findings = [
        *_ungrounded_findings(statements, evidence_ids, inferred_ids),
        *_missing_premise_findings(steps, premise_map, annotation_map),
        *_causality_findings(statements, annotation_map),
        *_scope_leap_findings(steps, annotation_map),
        *_direct_conflict_findings(statements, annotation_map),
        *_reported_limitation_findings(statements),
        *await _invalidated_source_findings(session, workspace_id),
    ]
    return await _reconcile_findings(session, workspace_id, findings)


def _ungrounded_findings(
    statements: list[Statement], evidence_ids: set[uuid.UUID], inferred_ids: set[uuid.UUID]
) -> list[Finding]:
    return [
        Finding(
            "ungrounded_statement",
            "statement",
            statement.id,
            {"message": "Statement has no linked excerpt or reasoning step"},
            "evidence_or_inference",
            "Ground this statement in a source excerpt or an explicit reasoning step.",
            "Link at least one excerpt or create a reasoning step concluding this statement.",
        )
        for statement in statements
        if statement.lifecycle == "proposed"
        and statement.assertion_mode in {"asserted", "reported"}
        and statement.id not in evidence_ids | inferred_ids
    ]


def _missing_premise_findings(
    steps: list[ReasoningStep],
    premise_map: dict[uuid.UUID, list[uuid.UUID]],
    annotations: dict[tuple[str, uuid.UUID], list[Annotation]],
) -> list[Finding]:
    findings = []
    for step in steps:
        if step.lifecycle == "rejected":
            continue
        required = [
            item.value
            for item in annotations.get(("reasoning_step", step.id), [])
            if item.type == "required_premise" and item.value.get("satisfied") is False
        ]
        if not premise_map.get(step.id) or required:
            findings.append(
                Finding(
                    "missing_premise",
                    "reasoning_step",
                    step.id,
                    {
                        "message": "Reasoning step has an unresolved required premise",
                        "requirements": required,
                    },
                    "missing_premise",
                    "Supply the premise needed for this reasoning step.",
                    "Add and link the required premise, then mark the requirement satisfied.",
                )
            )
    return findings


def _causality_findings(
    statements: list[Statement], annotations: dict[tuple[str, uuid.UUID], list[Annotation]]
) -> list[Finding]:
    findings = []
    for statement in statements:
        values = annotations.get(("statement", statement.id), [])
        causal = any(
            item.type == "claim_strength" and item.value.get("value") == "causal" for item in values
        )
        supported = any(
            item.type == "causal_support" and item.value.get("supported") is True for item in values
        )
        if statement.lifecycle != "rejected" and causal and not supported:
            findings.append(
                Finding(
                    "causality_overclaim",
                    "statement",
                    statement.id,
                    {"message": "Causal claim lacks a causal-support annotation"},
                    "causal_evidence",
                    "Establish that the causal wording is warranted.",
                    "Add a causal_support annotation tied to an appropriate design or revise the "
                    "claim.",
                )
            )
    return findings


def _scope_leap_findings(
    steps: list[ReasoningStep], annotations: dict[tuple[str, uuid.UUID], list[Annotation]]
) -> list[Finding]:
    findings = []
    for step in steps:
        for item in annotations.get(("reasoning_step", step.id), []):
            if item.type == "scope_transition" and item.value.get("justified") is False:
                findings.append(
                    Finding(
                        "scope_leap",
                        "reasoning_step",
                        step.id,
                        {"message": "Scope transition is not justified", "transition": item.value},
                        "scope_evidence",
                        "Justify this extrapolation across populations, settings, or outcomes.",
                        "Add evidence supporting the stated scope transition or narrow the "
                        "conclusion.",
                    )
                )
    return findings


def _direct_conflict_findings(
    statements: list[Statement], annotations: dict[tuple[str, uuid.UUID], list[Annotation]]
) -> list[Finding]:
    grouped: dict[str, list[tuple[Statement, str]]] = {}
    for statement in statements:
        if statement.lifecycle == "rejected":
            continue
        for item in annotations.get(("statement", statement.id), []):
            if (
                item.type == "claim_key"
                and item.value.get("key")
                and item.value.get("polarity") in {"supports", "refutes"}
            ):
                grouped.setdefault(item.value["key"], []).append(
                    (statement, item.value["polarity"])
                )
    findings = []
    for key, claims in grouped.items():
        if {polarity for _, polarity in claims} != {"supports", "refutes"}:
            continue
        for statement, polarity in claims:
            findings.append(
                Finding(
                    "direct_conflict",
                    "statement",
                    statement.id,
                    {
                        "message": "Opposing claims share a claim key",
                        "claim_key": key,
                        "polarity": polarity,
                    },
                    "conflict_resolution",
                    "Resolve or qualify the conflicting claims.",
                    "Explain the disagreement, add discriminating evidence, or reject/narrow one "
                    "claim.",
                )
            )
    return findings


def _reported_limitation_findings(statements: list[Statement]) -> list[Finding]:
    findings = []
    for statement in statements:
        if statement.lifecycle == "rejected":
            continue
        text = statement.text.casefold()
        markers = [marker for marker in LIMITATION_MARKERS if marker in text]
        if markers:
            findings.append(
                Finding(
                    "reported_limitation",
                    "statement",
                    statement.id,
                    {"message": "Source explicitly reports a study limitation", "markers": markers},
                    "study_limitation",
                    "Qualify conclusions affected by this source-reported limitation.",
                    "Retain the limitation with affected conclusions or add evidence that "
                    "addresses it.",
                )
            )
    return findings


async def _invalidated_source_findings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[Finding]:
    validities = list(
        await session.scalars(
            select(SourceValidity)
            .join(Source, Source.id == SourceValidity.source_id)
            .where(Source.workspace_id == workspace_id)
            .order_by(SourceValidity.source_id, SourceValidity.created_at.desc())
        )
    )
    latest: dict[uuid.UUID, SourceValidity] = {}
    for validity in validities:
        latest.setdefault(validity.source_id, validity)
    invalidated_source_ids = {
        source_id for source_id, validity in latest.items() if validity.status == "invalidated"
    }
    if not invalidated_source_ids:
        return []
    return [
        Finding(
            "invalidated_source",
            "source",
            source_id,
            {"message": "Source has been invalidated", "source_id": str(source_id)},
            "replacement_evidence",
            "Replace or qualify evidence from the invalidated source.",
            "Link a valid replacement source or reject/narrow claims grounded in this source.",
        )
        for source_id in sorted(invalidated_source_ids, key=str)
    ]


async def _reconcile_findings(
    session: AsyncSession, workspace_id: uuid.UUID, findings: list[Finding]
) -> VerificationResponse:
    active = {(item.rule_code, item.node_type, item.node_id): item for item in findings}
    issues = list(
        await session.scalars(
            select(Issue).where(Issue.workspace_id == workspace_id, Issue.rule_code.in_(RULE_CODES))
        )
    )
    obligations = list(
        await session.scalars(
            select(ProofObligation).where(
                ProofObligation.workspace_id == workspace_id,
                ProofObligation.generated_by_rule.in_(RULE_CODES),
            )
        )
    )
    issue_map = {(item.rule_code, item.node_type, item.node_id): item for item in issues}
    obligation_map = {
        (
            item.generated_by_rule,
            item.blocks_node_type or "statement",
            item.blocks_node_id or item.blocks_statement_id,
        ): item
        for item in obligations
    }
    issues_opened = issues_resolved = obligations_opened = obligations_resolved = 0

    for key, finding in active.items():
        issue = issue_map.get(key)
        obligation = obligation_map.get(key)
        if issue is None:
            session.add(
                Issue(
                    workspace_id=workspace_id,
                    rule_code=finding.rule_code,
                    node_type=finding.node_type,
                    node_id=finding.node_id,
                    details=finding.details,
                )
            )
            issues_opened += 1
        elif issue.status != "open":
            issue.status = "open"
            issue.details = finding.details
            issues_opened += 1
        if obligation is None:
            session.add(
                ProofObligation(
                    workspace_id=workspace_id,
                    kind=finding.obligation_kind,
                    description=finding.obligation_description,
                    required_condition=finding.required_condition,
                    blocks_statement_id=finding.node_id
                    if finding.node_type == "statement"
                    else None,
                    blocks_node_type=finding.node_type,
                    blocks_node_id=finding.node_id,
                    generated_by_rule=finding.rule_code,
                )
            )
            obligations_opened += 1
        elif obligation.status != "open":
            obligation.status = "open"
            obligations_opened += 1

    for key, issue in issue_map.items():
        if key not in active and issue.status == "open":
            issue.status = "resolved"
            issues_resolved += 1
    for key, obligation in obligation_map.items():
        if key not in active and obligation.status == "open":
            obligation.status = "resolved"
            obligations_resolved += 1

    event = GraphEvent(
        workspace_id=workspace_id,
        event_type="verification_run",
        idempotency_key=f"verify:{uuid.uuid4()}",
        payload={
            "rules": RULE_CODES,
            "issues_opened": issues_opened,
            "issues_resolved": issues_resolved,
            "obligations_opened": obligations_opened,
            "obligations_resolved": obligations_resolved,
        },
        provenance={"actor_type": "rule_engine", "actor_id": "deterministic_verifier_v2"},
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return VerificationResponse(
        verification_event_id=event.id,
        rules_run=RULE_CODES,
        issues_opened=issues_opened,
        issues_resolved=issues_resolved,
        obligations_opened=obligations_opened,
        obligations_resolved=obligations_resolved,
    )
