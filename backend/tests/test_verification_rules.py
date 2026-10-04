import uuid

from app.models import Annotation, ReasoningStep, Statement
from app.services.verification import (
    _causality_findings,
    _direct_conflict_findings,
    _missing_premise_findings,
    _reported_limitation_findings,
    _scope_leap_findings,
)


def statement() -> Statement:
    return Statement(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        text="A claim",
        assertion_mode="asserted",
        lifecycle="proposed",
        provenance={},
    )


def annotation(subject_type: str, subject_id: uuid.UUID, type_: str, value: dict) -> Annotation:
    return Annotation(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        subject_type=subject_type,
        subject_id=subject_id,
        type=type_,
        value=value,
        provenance={},
    )


def test_causality_rule_requires_explicit_causal_support() -> None:
    claim = statement()
    annotations = {
        ("statement", claim.id): [
            annotation("statement", claim.id, "claim_strength", {"value": "causal"})
        ]
    }

    findings = _causality_findings([claim], annotations)

    assert [item.rule_code for item in findings] == ["causality_overclaim"]


def test_direct_conflict_detects_opposing_polarities_for_the_same_claim_key() -> None:
    supports = statement()
    refutes = statement()
    annotations = {
        ("statement", supports.id): [
            annotation("statement", supports.id, "claim_key", {"key": "x", "polarity": "supports"})
        ],
        ("statement", refutes.id): [
            annotation("statement", refutes.id, "claim_key", {"key": "x", "polarity": "refutes"})
        ],
    }

    findings = _direct_conflict_findings([supports, refutes], annotations)

    assert {item.node_id for item in findings} == {supports.id, refutes.id}


def test_scope_and_missing_premise_annotations_create_reasoning_findings() -> None:
    step = ReasoningStep(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        conclusion_id=uuid.uuid4(),
        explanation="A step",
        lifecycle="proposed",
        provenance={},
    )
    annotations = {
        ("reasoning_step", step.id): [
            annotation("reasoning_step", step.id, "scope_transition", {"justified": False}),
            annotation("reasoning_step", step.id, "required_premise", {"satisfied": False}),
        ]
    }

    assert [item.rule_code for item in _scope_leap_findings([step], annotations)] == ["scope_leap"]
    assert [
        item.rule_code
        for item in _missing_premise_findings([step], {step.id: [uuid.uuid4()]}, annotations)
    ] == ["missing_premise"]


def test_reported_limitation_requires_explicit_limitation_language() -> None:
    limitation = statement()
    limitation.text = "The limited sample size may constrain generalizability."

    findings = _reported_limitation_findings([limitation, statement()])

    assert [item.rule_code for item in findings] == ["reported_limitation"]
