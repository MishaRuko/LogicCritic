import json
import uuid
from types import SimpleNamespace

from app.evaluations import scoring
from app.evaluations.europe_pmc import packet_from_xml
from app.evaluations.scifact_cases import to_eval_case, verdict_cases
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
                    "question": (
                        "Does the evidence establish that the intervention caused the outcome?"
                    ),
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
        "<article><front><article-title>Study title</article-title>"
        "<abstract>Abstract text</abstract></front>"
        "<body><sec><title>Results</title><p>Observed result.</p></sec></body>"
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


# -- v3 scoring ----------------------------------------------------------------------------------


KEY = {
    "verdict_options": scoring.PAPER_VERDICTS,
    "expected_verdict": "partially_supported",
    "required_deductions": [
        {"deduction": "a", "evidence": "x"},
        {"deduction": "b", "evidence": "y"},
    ],
}


def test_metrics_count_errors_and_ignore_claim_volume_for_precision() -> None:
    raw = {
        "claims": [
            {"claim": "n=120", "status": "supported"},
            {"claim": "28 months", "status": "contradicted"},
            {"claim": "mainly phenobarbital", "status": "unsupported"},
            {"claim": "double blind", "status": "supported"},
        ],
        "verdict_given": "partially_supported",
        "deductions": [{"index": 0, "status": "valid"}, {"index": 1, "status": "flawed"}],
        "invalid_inferences": ["non-significance read as equivalence"],
    }

    result = scoring.metrics(raw, KEY)

    assert result["claims_contradicted"] == 1 and result["claims_unsupported"] == 1
    assert result["precision"] == 0.5
    assert result["errors"] == 2  # contradicted + invalid inference; omissions never count
    assert result["verdict_correct"] is True
    assert result["deduction_recall"] == 0.5 and result["deductions_flawed"] == 1


def test_missing_deduction_reports_count_as_absent() -> None:
    raw = {"claims": [], "verdict_given": "none", "deductions": [], "invalid_inferences": []}

    result = scoring.metrics(raw, KEY)

    assert result["deduction_recall"] == 0 and result["precision"] is None
    assert result["verdict_correct"] is False


def test_explicit_scifact_verdict_is_read_deterministically() -> None:
    options = scoring.SCIFACT_VERDICTS
    assert (
        scoring.explicit_verdict("Verdict: **NOT ENOUGH INFO**\nThe abstract...", options)
        == "NOT ENOUGH INFO"
    )
    assert scoring.explicit_verdict("verdict: contradicts", options) == "CONTRADICTS"
    assert scoring.explicit_verdict("It supports the claim.", options) is None


def test_paired_summary_reports_guarded_minus_baseline() -> None:
    pairs = [({"errors": 3}, {"errors": 1}), ({"errors": 2}, {"errors": 0})]

    row = next(r for r in scoring.paired_summary(pairs) if r["metric"] == "errors")

    assert row["baseline"] == 2.5 and row["guarded"] == 0.5 and row["diff"] == -2.0
    assert row["ci_low"] <= row["diff"] <= row["ci_high"]


def test_scifact_cases_include_not_enough_info_and_gold_labels() -> None:
    corpus = [
        {"doc_id": 1, "title": "T", "abstract": ["S0.", "S1."]},
        {"doc_id": 2, "title": "U", "abstract": ["Z."]},
    ]
    claims = [
        {
            "id": 7,
            "claim": "X helps.",
            "cited_doc_ids": [1, 2],
            "evidence": {"1": [{"label": "CONTRADICT", "sentences": [1]}]},
        },
    ]

    cases = verdict_cases(corpus, claims)
    built = to_eval_case(cases[0])

    assert [c["label"] for c in cases] == ["CONTRADICTS", "NOT ENOUGH INFO"]
    assert built["gold_label"] == "CONTRADICTS" and "Verdict: SUPPORTS" in built["question"]
    assert "gold_label" not in built["question"]
    assert built["packet"]["sources"][0]["text"] == "# T\n\nS0.\n\nS1."


async def test_scifact_key_is_the_gold_label_and_scorer_never_sees_it() -> None:
    from types import SimpleNamespace

    from tests.agent_helpers import reply, text

    class Client:
        def __init__(self):
            self.requests = []
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **request):
            self.requests.append(request)
            return reply(
                text(
                    json.dumps(
                        {
                            "claims": [
                                {"claim": "S1 says no effect", "status": "supported", "note": ""}
                            ],
                            "verdict_given": "SUPPORTS",
                            "invalid_inferences": [],
                            "deductions": [],
                        }
                    )
                )
            )

    client = Client()
    tally = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    key = await scoring.derive_key(
        client, "m", {"gold_label": "CONTRADICTS", "question": "q"}, {}, tally
    )
    _, result = await scoring.score_answer(
        client, "m", "q", {"sources": []}, key, "Verdict: CONTRADICTS. S1 shows no effect.", tally
    )

    sent = client.requests[0]["messages"][0]["content"]
    assert "expected_verdict" not in sent and "SciFact expert label" not in sent
    assert result["verdict_given"] == "CONTRADICTS"  # the explicit line wins over the scorer
    assert result["verdict_correct"] is True


async def test_cost_cap_ignores_arms_still_running() -> None:
    from sqlalchemy import select

    from app.database import engine, session_factory
    from app.evaluations.service import _spent, create_evaluation
    from app.models import EvaluationCase, EvaluationOutput
    from tests.agent_helpers import make_world

    await engine.dispose()
    world = await make_world()
    evaluation_id = await create_evaluation("t", [{"packet": {"sources": []}, "question": "q?"}])
    async with session_factory() as session:
        case = await session.scalar(
            select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id)
        )
        session.add(
            EvaluationOutput(
                evaluation_case_id=case.id,
                arm="baseline",
                workspace_id=world.workspace_id,
                agent_run_id=world.run_id,
                status="running",
                usage={},
            )
        )
        await session.commit()

    try:
        assert await _spent(evaluation_id, sessions=session_factory) == 0
    finally:
        await engine.dispose()  # pooled connections are bound to this test's event loop
