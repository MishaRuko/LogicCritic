"""Explicit SciFact download and manifest generation for external regression evaluation."""

import argparse
import json
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.request import urlretrieve

SCIFACT_ARCHIVE_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"


@dataclass(frozen=True)
class SciFactCase:
    claim_id: int
    claim: str
    document_id: int
    title: str
    abstract: list[str]
    label: str
    rationale_sentence_ids: list[int]


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_dev_manifest(corpus: list[dict], claims: list[dict], limit: int | None = None) -> list[SciFactCase]:
    documents = {item["doc_id"]: item for item in corpus}
    cases: list[SciFactCase] = []
    for claim in claims:
        for document_id_text, evidence_sets in claim.get("evidence", {}).items():
            document_id = int(document_id_text)
            document = documents.get(document_id)
            if document is None:
                continue
            for evidence in evidence_sets:
                cases.append(
                    SciFactCase(
                        claim_id=claim["id"],
                        claim=claim["claim"],
                        document_id=document_id,
                        title=document["title"],
                        abstract=document["abstract"],
                        label=evidence["label"],
                        rationale_sentence_ids=evidence.get("sentences", []),
                    )
                )
                if limit is not None and len(cases) >= limit:
                    return cases
    return cases


def download_and_build_manifest(output_dir: Path, limit: int | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / "scifact_data.tar.gz"
    if not archive.exists():
        urlretrieve(SCIFACT_ARCHIVE_URL, archive)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(output_dir, filter="data")
    data_dir = output_dir / "data"
    cases = build_dev_manifest(
        read_jsonl(data_dir / "corpus.jsonl"),
        read_jsonl(data_dir / "claims_dev.jsonl"),
        limit,
    )
    manifest = output_dir / "scifact_dev_manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case)) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Download SciFact and build a development-set evaluation manifest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(download_and_build_manifest(args.output_dir, args.limit))


if __name__ == "__main__":
    main()
