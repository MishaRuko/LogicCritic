"""CLI for stored blind LogicCritic evaluations.

The JSONL manifest uses one object per case. Each object has `packet` with `sources` (`title`,
`text`), plus `question`, `completion_criteria`, `rubric`, and optional `trap`.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from app.evaluations.europe_pmc import fetch_packets
from app.evaluations.service import create_evaluation, generate_cases, render_report, run_evaluation


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
    await run_evaluation(uuid.UUID(args.evaluation_id))


async def _report(args) -> None:
    print(await render_report(uuid.UUID(args.evaluation_id)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a stored blind baseline-versus-guarded evaluation")
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
    run.set_defaults(func=_run)
    report = commands.add_parser("report")
    report.add_argument("evaluation_id")
    report.set_defaults(func=_report)
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
