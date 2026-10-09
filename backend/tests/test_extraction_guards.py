import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas import ExtractedReasoningStep, ExtractedStatement, ExtractionOutput
from app.services.claude_errors import ensure_complete
from app.services.extraction import (
    drop_duplicate_statements,
    drop_orphan_supporting,
    drop_unresolvable_steps,
    extraction_schema,
    is_extractable_excerpt,
)


def statement(ref: str, salience: str = "core", supports: str | None = None) -> ExtractedStatement:
    return ExtractedStatement(
        client_ref=ref,
        text=f"Statement {ref}.",
        assertion_mode="asserted",
        salience=salience,
        supports_ref=supports,
        excerpt_ids=[uuid.uuid4()],
    )


def step(ref: str, premises: list[str], conclusion: str) -> ExtractedReasoningStep:
    return ExtractedReasoningStep(
        client_ref=ref, premise_refs=premises, conclusion_ref=conclusion, explanation="Because."
    )


def test_both_lists_are_required_so_the_model_cannot_return_only_steps() -> None:
    schema = extraction_schema()
    assert schema["required"] == ["statements", "reasoning_steps"]
    assert schema["additionalProperties"] is False


def test_reference_fields_say_they_are_not_excerpt_ids() -> None:
    defs = extraction_schema()["$defs"]
    assert (
        "Never excerpt IDs"
        in defs["ExtractedReasoningStep"]["properties"]["premise_refs"]["description"]
    )
    assert "excerpts" in defs["ExtractedStatement"]["properties"]["excerpt_ids"]["description"]
    assert set(defs["ExtractedStatement"]["properties"]["salience"]["enum"]) == {
        "core",
        "secondary",
        "supporting",
    }


def test_extraction_has_no_fixed_claim_count_cap() -> None:
    properties = extraction_schema()["properties"]
    assert "maxItems" not in properties["statements"]
    assert "maxItems" not in properties["reasoning_steps"]


def test_retraction_notice_is_retained_but_not_extracted() -> None:
    title = SimpleNamespace(locator={"jsonPath": "title"})
    abstract = SimpleNamespace(locator={"jsonPath": "abstract"})
    notice = SimpleNamespace(locator={"jsonPath": "abstract", "section": "Retraction Notice"})
    conclusion = SimpleNamespace(locator={"section": "Conclusion"})

    assert is_extractable_excerpt(title) is False
    assert is_extractable_excerpt(abstract) is True
    assert is_extractable_excerpt(abstract, fulltext_available=True) is False
    assert is_extractable_excerpt(notice) is False
    assert is_extractable_excerpt(conclusion) is True


def test_duplicate_source_claims_and_dependent_steps_are_dropped() -> None:
    output = ExtractionOutput(
        statements=[statement("duplicate"), statement("new")],
        reasoning_steps=[step("r1", ["duplicate"], "new")],
    )
    output.statements[0].text = "  Already   extracted. "

    deduplicated = drop_duplicate_statements(output, ["already extracted."])

    assert [item.client_ref for item in deduplicated.statements] == ["new"]
    assert deduplicated.reasoning_steps == []


def test_steps_citing_statements_that_exist_are_kept() -> None:
    output = ExtractionOutput(
        statements=[statement("s1"), statement("s2"), statement("s3")],
        reasoning_steps=[step("r1", ["s1", "s2"], "s3")],
    )
    assert [item.client_ref for item in drop_unresolvable_steps(output).reasoning_steps] == ["r1"]


def test_steps_citing_unknown_statements_are_dropped_but_statements_survive() -> None:
    excerpt_id = str(uuid.uuid4())  # the model sometimes puts an excerpt ID where a ref belongs
    output = ExtractionOutput(
        statements=[statement("s1"), statement("s2")],
        reasoning_steps=[
            step("r1", [excerpt_id], "step1_conclusion"),
            step("r2", ["s1"], "missing"),
            step("r3", ["s1"], "s2"),
        ],
    )
    cleaned = drop_unresolvable_steps(output)
    assert [item.client_ref for item in cleaned.reasoning_steps] == ["r3"]
    assert len(cleaned.statements) == 2


def test_a_cut_off_answer_is_refused_rather_than_used() -> None:
    with pytest.raises(HTTPException) as error:
        ensure_complete({"stop_reason": "max_tokens", "content": []}, "extraction")
    assert error.value.status_code == 502 and "cut off" in error.value.detail


@pytest.mark.parametrize("reason", ["tool_use", "end_turn", None])
def test_complete_answers_pass(reason: str | None) -> None:
    ensure_complete({"stop_reason": reason}, "extraction")


def refs(output: ExtractionOutput) -> list[str]:
    return [item.client_ref for item in output.statements]


def test_supporting_claims_survive_only_if_they_support_something_kept() -> None:
    output = ExtractionOutput(
        statements=[
            statement("result"),
            statement("detail", "secondary"),
            statement("design", "supporting", supports="result"),
            statement("figure", "supporting", supports="detail"),
            statement("background", "supporting"),  # supports nothing
            statement("dangling", "supporting", supports="gone"),  # supports a non-claim
            statement("chain", "supporting", supports="design"),  # supports only a supporting one
        ]
    )
    assert refs(drop_orphan_supporting(output)) == ["result", "detail", "design", "figure"]


def test_a_supporting_premise_of_a_step_is_kept_even_without_supports_ref() -> None:
    output = ExtractionOutput(
        statements=[statement("a", "supporting"), statement("b"), statement("c", "supporting")],
        reasoning_steps=[step("r1", ["a"], "b"), step("r2", ["c"], "a")],
    )
    # c only feeds a supporting claim, so it is not anchored; a feeds a kept conclusion
    assert refs(drop_orphan_supporting(output)) == ["a", "b"]


def test_dropped_background_takes_dependent_steps_with_it() -> None:
    output = ExtractionOutput(
        statements=[statement("x"), statement("bg", "supporting")],
        reasoning_steps=[step("r1", ["bg"], "x")],
    )
    kept = drop_unresolvable_steps(drop_orphan_supporting(output))
    assert refs(kept) == ["x", "bg"]  # a premise of a kept conclusion is not background
    assert [s.client_ref for s in kept.reasoning_steps] == ["r1"]


def test_extraction_records_what_a_claim_asserts_so_the_causality_rule_applies_to_papers() -> None:
    from app.services.extraction import _strength_annotations
    from app.services.verification import _causality_findings

    def claim(ref: str, strength: str | None, design: str | None) -> ExtractedStatement:
        return statement(ref).model_copy(
            update={"claim_strength": strength, "study_design": design}
        )

    output = ExtractionOutput(
        statements=[
            claim("rct", "causal", "randomised_trial"),
            claim("cohort", "causal", "observational"),  # a paper overclaiming
            claim("unsaid", "causal", None),  # causal, design not stated
            claim("link", "associative", None),
            claim("plain", None, None),
        ],
        reasoning_steps=[],
    )
    from app.schemas import ProvenanceInput

    provenance = ProvenanceInput(actor_type="extractor", actor_id="anthropic")
    operations = _strength_annotations(output, provenance)
    assert [(o.subject_id, o.type) for o in operations] == [
        ("rct", "claim_strength"),
        ("rct", "causal_support"),
        ("cohort", "claim_strength"),
        ("cohort", "causal_support"),
        ("unsaid", "claim_strength"),
        ("link", "claim_strength"),
    ]
    assert [o.value["supported"] for o in operations if o.type == "causal_support"] == [True, False]

    # Applied to the graph, the verifier flags the two causal claims without a causal design.
    from app.models import Annotation, Statement

    ids = {ref: uuid.uuid4() for ref in ("rct", "cohort", "unsaid", "link")}
    statements = [
        Statement(id=i, text=ref, assertion_mode="asserted", lifecycle="proposed")
        for ref, i in ids.items()
    ]
    annotations: dict = {}
    for o in operations:
        annotations.setdefault(("statement", ids[o.subject_id]), []).append(
            Annotation(type=o.type, value=o.value)
        )
    flagged = {item.node_id for item in _causality_findings(statements, annotations)}
    assert flagged == {ids["cohort"], ids["unsaid"]}


def test_the_extraction_schema_asks_for_strength_and_design() -> None:
    fields = extraction_schema()["$defs"]["ExtractedStatement"]["properties"]
    assert {"claim_strength", "study_design"} <= set(fields)
