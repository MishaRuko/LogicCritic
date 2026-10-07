"""CLI for stored blind LogicCritic evaluations.

The JSONL manifest uses one object per case. Each object has `packet` with `sources` (`title`,
`text`), plus `question`, `completion_criteria`, `rubric`, and optional `trap`.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.database import session_factory
from app.evaluations.europe_pmc import fetch_packet, fetch_packets
from app.evaluations.scifact_cases import balanced_sample
from app.evaluations.service import (
    create_evaluation,
    generate_cases,
    render_report,
    run_evaluation,
    score_evaluation,
)
from app.models import EvaluationCase


def _read_manifest(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _generate(args) -> None:
    packet = json.loads(args.packet.read_text())
    cases = await generate_cases(packet, args.count)
    with args.output.open("w") as handle:
        for case in cases:
            handle.write(json.dumps({"packet": packet, **case}) + "\n")


async def _fetch_pmc(args) -> None:
    for path in await fetch_packets(args.pmcids, args.output_dir):
        print(path)


async def _create(args) -> None:
    cases = [case for manifest in args.manifests for case in _read_manifest(manifest)]
    print(await create_evaluation(args.name, cases))


async def _run(args) -> None:
    await run_evaluation(
        uuid.UUID(args.evaluation_id), concurrency=args.concurrency, retry_failed=args.retry_failed
    )
    print(await render_report(uuid.UUID(args.evaluation_id)))


async def _score(args) -> None:
    await score_evaluation(uuid.UUID(args.evaluation_id), concurrency=args.concurrency)
    print(await render_report(uuid.UUID(args.evaluation_id)))


async def _scifact(args) -> None:
    cases = balanced_sample(args.per_label, seed=args.seed)
    evaluation_id = await create_evaluation(
        args.name, cases, extra_config={"benchmark": "scifact-dev"}
    )
    print("evaluation", evaluation_id, flush=True)
    await run_evaluation(evaluation_id, concurrency=args.concurrency)
    print(await render_report(evaluation_id))


async def _paper_pilot(args) -> None:
    """Reuse an earlier evaluation's questions on freshly fetched packets, plus new cases."""
    async with session_factory() as session:
        old = list(
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.evaluation_run_id == uuid.UUID(args.from_evaluation))
                .order_by(EvaluationCase.position)
            )
        )
    packets = {}
    for case in old:
        if case.packet["id"] not in packets:
            packets[case.packet["id"]] = await fetch_packet(case.packet["id"])
    keep = ("question", "completion_criteria", "rubric", "trap")
    cases = [
        {"packet": packets[c.packet["id"]], **{k: c.rubric[k] for k in keep if k in c.rubric}}
        for c in old
    ]
    for pmcid, packet in packets.items():
        if args.extra_per_packet <= 0:
            break
        avoid = [c["question"] for c in cases if c["packet"]["id"] == pmcid]
        try:
            new = await generate_cases(packet, args.extra_per_packet, avoid=avoid)
        except Exception as error:  # noqa: BLE001 - a refused packet must not stop the pilot
            print(f"{pmcid}: case generation failed: {error}", flush=True)
            continue
        cases += [{"packet": packet, **case} for case in new]
        print(f"{pmcid}: {len(new)} new cases", flush=True)
    evaluation_id = await create_evaluation(args.name, cases)
    print("evaluation", evaluation_id, f"({len(cases)} cases)", flush=True)
    await run_evaluation(evaluation_id, concurrency=args.concurrency)
    print(await render_report(evaluation_id))


async def _report(args) -> None:
    print(await render_report(uuid.UUID(args.evaluation_id)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a stored blind baseline-versus-guarded evaluation"
    )
    commands = parser.add_subparsers(required=True)
    fetch_pmc = commands.add_parser("fetch-pmc")
    fetch_pmc.add_argument("pmcids", nargs="+")
    fetch_pmc.add_argument("--output-dir", type=Path, required=True)
    fetch_pmc.set_defaults(func=_fetch_pmc)
    generate = commands.add_parser("generate")
    generate.add_argument("--packet", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--count", type=int, default=3)
    generate.set_defaults(func=_generate)
    create = commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--manifest", dest="manifests", type=Path, action="append", required=True)
    create.set_defaults(func=_create)
    run = commands.add_parser("run")
    run.add_argument("evaluation_id")
    run.add_argument("--concurrency", type=int, default=5)
    run.add_argument("--retry-failed", action="store_true")
    run.set_defaults(func=_run)
    score = commands.add_parser("score", help="score stored answers without re-running agents")
    score.add_argument("evaluation_id")
    score.add_argument("--concurrency", type=int, default=6)
    score.set_defaults(func=_score)
    scifact = commands.add_parser("scifact", help="gold-labelled SciFact claim verification")
    scifact.add_argument("--name", required=True)
    scifact.add_argument("--per-label", type=int, default=20)
    scifact.add_argument("--seed", type=int, default=0)
    scifact.add_argument("--concurrency", type=int, default=6)
    scifact.set_defaults(func=_scifact)
    pilot = commands.add_parser("paper-pilot", help="re-run an earlier paper evaluation's cases")
    pilot.add_argument("--name", required=True)
    pilot.add_argument("--from-evaluation", required=True)
    pilot.add_argument("--extra-per-packet", type=int, default=0)
    pilot.add_argument("--concurrency", type=int, default=5)
    pilot.set_defaults(func=_paper_pilot)
    report = commands.add_parser("report")
    report.add_argument("evaluation_id")
    report.set_defaults(func=_report)
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
