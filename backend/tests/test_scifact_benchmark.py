from app.benchmarks.scifact import build_dev_manifest


def test_builds_claim_evidence_cases_with_gold_labels() -> None:
    corpus = [{"doc_id": 7, "title": "A paper", "abstract": ["First sentence", "Second sentence"]}]
    claims = [
        {
            "id": 3,
            "claim": "A claim",
            "evidence": {"7": [{"label": "SUPPORT", "sentences": [1]}]},
        }
    ]

    cases = build_dev_manifest(corpus, claims)

    assert len(cases) == 1
    assert cases[0].label == "SUPPORT"
    assert cases[0].rationale_sentence_ids == [1]
