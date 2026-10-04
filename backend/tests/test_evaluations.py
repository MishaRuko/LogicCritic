import json
from types import SimpleNamespace
import uuid

from app.evaluations.europe_pmc import packet_from_xml
from app.evaluations.service import (
    GeneratedCases,
    PairwiseVerdict,
    _arm_payload,
    _blind_answers,
    packet_hash,
)
from app.services.text_ingestion import MAX_EXCERPT_CHARS, parse_structured_text


def test_packet_hash_is_stable_when_json_key_order_changes() -> None:
    first = {"sources": [{"title": "Paper", "text": "Evidence"}], "id": "case-1"}
    second = json.loads('{"id":"case-1","sources":[{"text":"Evidence","title":"Paper"}]}')

    assert packet_hash(first) == packet_hash(second)


def test_generated_case_requires_a_question_and_hidden_rubric() -> None:
    cases = GeneratedCases.model_validate(
        {
            "cases": [
                {
                    "question": "Does the evidence establish that the intervention caused the outcome?",
                    "completion_criteria": ["Identify the study design", "Account for limitations"],
                    "rubric": ["It is observational", "It cannot establish causation"],
                    "trap": "The abstract uses causal language despite observational methods.",
                }
            ]
        }
    )

    assert cases.cases[0].rubric[1] == "It cannot establish causation"


def test_pairwise_verdict_keeps_scores_and_anonymous_winner() -> None:
    verdict = PairwiseVerdict.model_validate(
        {
            "answer_a_score": 2,
            "answer_b_score": 4,
            "winner": "B",
            "critical_errors_a": ["Causal overclaim"],
            "critical_errors_b": [],
            "rationale": "B correctly qualifies the observational result.",
        }
    )

    assert verdict.winner == "B"
    assert verdict.answer_b_score > verdict.answer_a_score


def test_europe_pmc_packet_keeps_article_body_and_drops_references() -> None:
    packet = packet_from_xml(
        "PMC123",
        "<article><front><article-title>Study title</article-title><abstract>Abstract text</abstract>"
        "</front><body><sec><title>Results</title><p>Observed result.</p></sec></body>"
        "<ref-list><ref>Reference omitted.</ref></ref-list></article>",
    )

    assert packet["id"] == "PMC123"
    assert "Observed result." in packet["sources"][0]["text"]
    assert "Reference omitted." not in packet["sources"][0]["text"]
    assert "### Results" in packet["sources"][0]["text"]


def test_evaluation_arm_never_receives_answer_specific_criteria() -> None:
    case = SimpleNamespace(
        id=uuid.uuid4(),
        rubric={
            "question": "What does the evidence support?",
            "completion_criteria": ["The answer-key fact is 42."],
        },
    )
    config = {
        "arm_model": "claude-sonnet-5-5",
        "baseline_max_turns": 6,
        "guarded_max_turns": 12,
        "max_web_searches": 0,
    }

    baseline = _arm_payload(case, "baseline", config)
    guarded = _arm_payload(case, "guarded", config)

    assert baseline.completion_criteria == guarded.completion_criteria == []
    assert baseline.max_turns == 6 and guarded.max_turns == 12


def test_blind_answers_are_always_ordered_by_label_and_strip_only_system_marker() -> None:
    outputs = {
        "baseline": SimpleNamespace(answer="Baseline [95% CI]."),
        "guarded": SimpleNamespace(
            answer="Guarded answer.\n\n[Verifier record] Final certainty: conditional."
        ),
    }

    answers = _blind_answers({"baseline": "B", "guarded": "A"}, outputs)

    assert list(answers) == ["A", "B"]
    assert answers == {"A": "Guarded answer.", "B": "Baseline [95% CI]."}


def test_europe_pmc_article_becomes_bounded_sectioned_excerpts() -> None:
    paragraph = " ".join(f"Result {index}." for index in range(500))
    packet = packet_from_xml(
        "PMC456",
        f"<article><front><article-title>Title</article-title><abstract>Summary</abstract></front>"
        f"<body><sec><title>Methods</title><p>{paragraph}</p></sec></body></article>",
    )

    excerpts = parse_structured_text(packet["sources"][0]["text"])

    assert len(excerpts) > 5
    assert all(len(item.text) <= MAX_EXCERPT_CHARS for item in excerpts)
    assert any(item.locator.get("section") == "Methods" for item in excerpts)
