import json

from app.evaluations.service import GeneratedCases, PairwiseVerdict, packet_hash


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
