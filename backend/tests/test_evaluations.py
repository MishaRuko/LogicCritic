import json

from app.evaluations.service import GeneratedCases, PairwiseVerdict, packet_hash
from app.evaluations.europe_pmc import packet_from_xml


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
