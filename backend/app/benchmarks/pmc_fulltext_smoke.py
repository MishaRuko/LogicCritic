"""Run an open PubMed Central article through the live extraction API."""

import argparse
import json
from xml.etree import ElementTree

import httpx

from app.benchmarks.scifact_smoke import wait_for_job


def fetch_article(pmc_id: str) -> tuple[str, str]:
    numeric_id = pmc_id.removeprefix("PMC")
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={numeric_id}"
    response = httpx.get(
        url, headers={"User-Agent": "LogicCritic full-text smoke test"}, timeout=60
    )
    response.raise_for_status()
    article = ElementTree.fromstring(response.content)
    title = "".join(article.find(".//article-title").itertext()).strip()
    body = ["".join(item.itertext()).strip() for item in article.findall(".//body//p")]
    return title, "# " + title + "\n\n" + "\n\n".join(body)


def run(pmc_id: str, api_url: str, timeout_seconds: int) -> dict:
    title, content = fetch_article(pmc_id)
    with httpx.Client(base_url=api_url, timeout=60) as client:
        workspace_response = client.post(
            "/workspaces", json={"title": f"PMC full-text smoke: {pmc_id}"}
        )
        workspace_response.raise_for_status()
        workspace = workspace_response.json()
        upload = client.post(
            f"/workspaces/{workspace['id']}/sources",
            files={"file": (f"{pmc_id}.md", content, "text/markdown")},
        )
        upload.raise_for_status()
        source = upload.json()
        job_response = client.post(
            f"/sources/{source['id']}/extract",
            json={"idempotency_key": f"pmc-fulltext-smoke-{pmc_id}"},
        )
        job_response.raise_for_status()
        job = wait_for_job(client, job_response.json()["id"], timeout_seconds)
        graph_response = client.get(f"/workspaces/{workspace['id']}/graph")
        graph_response.raise_for_status()
        verification_response = client.post(f"/workspaces/{workspace['id']}/verify")
        verification_response.raise_for_status()

    graph = graph_response.json()
    statements = {item["id"]: item["text"] for item in graph["statements"]}
    chains = [
        {
            "premises": [statements[premise_id] for premise_id in step["premise_ids"]],
            "conclusion": statements[step["conclusion_id"]],
            "explanation": step["explanation"],
        }
        for step in graph["reasoning_steps"][:10]
    ]
    return {
        "pmc_id": pmc_id,
        "title": title,
        "source_characters": len(content),
        "workspace_id": workspace["id"],
        "job": job,
        "statement_count": len(graph["statements"]),
        "reasoning_step_count": len(graph["reasoning_steps"]),
        "verification": verification_response.json(),
        "sample_chains": chains,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an open PMC full text through the live extractor"
    )
    parser.add_argument("--pmc-id", default="PMC3805509")
    parser.add_argument("--api-url", default="http://localhost:8000/api")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    print(json.dumps(run(args.pmc_id, args.api_url, args.timeout_seconds), indent=2))


if __name__ == "__main__":
    main()
