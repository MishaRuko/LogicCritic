"""SciFact claim-verification cases with expert gold labels, for the agent evaluation.

One case per (claim, cited abstract): SUPPORTS or CONTRADICTS where the annotators found
rationale sentences, NOT ENOUGH INFO where the abstract was cited but holds no evidence. The
last kind tests overclaiming: the right answer is to decline.
"""

import json
import random
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlretrieve

from app.benchmarks.scifact import SCIFACT_ARCHIVE_URL, read_jsonl

LABELS = {"SUPPORT": "SUPPORTS", "CONTRADICT": "CONTRADICTS"}
QUESTION = (
    'Claim: "{claim}"\n\nUsing only the supplied abstract, does it support the claim, contradict '
    "it, or not give enough information to decide? Begin your answer with exactly one of "
    "'Verdict: SUPPORTS', 'Verdict: CONTRADICTS' or 'Verdict: NOT ENOUGH INFO', then give the "
    "deduction from the abstract that leads to it."
)


def verdict_cases(corpus: list[dict], claims: list[dict]) -> list[dict]:
    documents = {item["doc_id"]: item for item in corpus}
    cases = []
    for claim in claims:
        evidence = claim.get("evidence", {})
        for doc_id in claim.get("cited_doc_ids", []):
            document = documents.get(doc_id)
            if document is None:
                continue
            sets = evidence.get(str(doc_id))
            label = LABELS[sets[0]["label"]] if sets else "NOT ENOUGH INFO"
            cases.append({"claim_id": claim["id"], "claim": claim["claim"], "document": document, "label": label})
    return cases


def to_eval_case(item: dict) -> dict:
    document = item["document"]
    # One sentence per paragraph, so every sentence is its own citable excerpt.
    text = f"# {document['title']}\n\n" + "\n\n".join(s.strip() for s in document["abstract"])
    return {
        "packet": {
            "id": f"scifact-{item['claim_id']}-{document['doc_id']}",
            "sources": [{"title": document["title"], "text": text}],
        },
        "question": QUESTION.format(claim=item["claim"]),
        "gold_label": item["label"],
        "scifact_claim_id": item["claim_id"],
        "scifact_doc_id": document["doc_id"],
    }


def balanced_sample(per_label: int, seed: int = 0) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "scifact.tar.gz"
        urlretrieve(SCIFACT_ARCHIVE_URL, archive)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        data = Path(tmp) / "data"
        cases = verdict_cases(read_jsonl(data / "corpus.jsonl"), read_jsonl(data / "claims_dev.jsonl"))
    rng = random.Random(seed)
    picked = []
    for label in ("SUPPORTS", "CONTRADICTS", "NOT ENOUGH INFO"):
        pool = [case for case in cases if case["label"] == label]
        rng.shuffle(pool)
        picked += pool[:per_label]
    rng.shuffle(picked)
    return [to_eval_case(item) for item in picked]


if __name__ == "__main__":
    print(json.dumps(balanced_sample(1), indent=2)[:2000])
