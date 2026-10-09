import uuid

from app.models import Annotation, ReasoningStep, Statement
from app.services.verification import (
    _causality_findings,
    _direct_conflict_findings,
    _missing_premise_findings,
    _reported_limitation_findings,
    _scope_leap_findings,
    _ungrounded_findings,
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
    premises = {step.id: [uuid.uuid4()]}
    assert [
        item.rule_code for item in _missing_premise_findings([step], premises, annotations, set())
    ] == ["missing_premise"]

    # The critic's flag clears when the step is replaced by a revision, or a person accepts it.
    assert _missing_premise_findings([step], premises, annotations, {step.id}) == []
    step.lifecycle = "accepted"
    assert _missing_premise_findings([step], premises, annotations, set()) == []
    # Accepting a step with no premises at all does not make it reasoning.
    assert len(_missing_premise_findings([step], {step.id: []}, annotations, set())) == 1


def test_accepting_a_claim_does_not_hide_that_it_has_no_source() -> None:
    claim = statement()
    claim.lifecycle = "accepted"
    findings = _ungrounded_findings([claim], evidence_ids=set(), inferred_ids=set())
    assert [item.rule_code for item in findings] == ["ungrounded_statement"]
    claim.lifecycle = "rejected"
    assert _ungrounded_findings([claim], evidence_ids=set(), inferred_ids=set()) == []


def test_reported_limitation_requires_explicit_limitation_language() -> None:
    limitation = statement()
    limitation.text = "The limited sample size may constrain generalizability."

    findings = _reported_limitation_findings([limitation, statement()])

    assert [item.rule_code for item in findings] == ["reported_limitation"]
