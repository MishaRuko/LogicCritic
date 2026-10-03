"""Run a small SciFact sample through the live extraction API for manual inspection."""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import httpx


def load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def wait_for_job(client: httpx.Client, job_id: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = client.get(f"/extraction-jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(2)
    raise TimeoutError(f"Extraction job {job_id} did not finish within {timeout_seconds} seconds")


def run(manifest: Path, api_url: str, timeout_seconds: int) -> dict:
    cases = load_cases(manifest)
    by_document: dict[int, list[dict]] = defaultdict(list)
    for case in cases:
        by_document[case["document_id"]].append(case)

    with httpx.Client(base_url=api_url, timeout=30) as client:
        workspace_response = client.post("/workspaces", json={"title": "SciFact extraction smoke test"})
        workspace_response.raise_for_status()
        workspace = workspace_response.json()
        excerpt_sources: dict[str, int] = {}
        jobs = []
        for document_id, document_cases in by_document.items():
            case = document_cases[0]
            content = f"# {case['title']}\n\n" + "\n\n".join(case["abstract"])
            upload = client.post(
                f"/workspaces/{workspace['id']}/sources",
                files={"file": (f"scifact-{document_id}.md", content, "text/markdown")},
            )
            upload.raise_for_status()
            source = upload.json()
            for excerpt in source["excerpts"]:
                excerpt_sources[excerpt["id"]] = document_id
            job = client.post(
                f"/sources/{source['id']}/extract",
                json={"idempotency_key": f"scifact-smoke-{document_id}"},
            )
            job.raise_for_status()
            jobs.append(wait_for_job(client, job.json()["id"], timeout_seconds))

        graph = client.get(f"/workspaces/{workspace['id']}/graph")
        graph.raise_for_status()
        statements = defaultdict(list)
        for statement in graph.json()["statements"]:
            document_ids = sorted({excerpt_sources[item] for item in statement["excerpt_ids"]})
            for document_id in document_ids:
                statements[document_id].append(statement["text"])

    return {
        "workspace_id": workspace["id"],
        "jobs": jobs,
        "gold_cases": cases,
        "extracted_statements_by_document": statements,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small SciFact manifest through the live extractor")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--api-url", default="http://localhost:8000/api")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.api_url, args.timeout_seconds), indent=2))


if __name__ == "__main__":
    main()
