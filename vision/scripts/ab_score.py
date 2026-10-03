"""Score the runs under runs/ab/ against the LSV labels and print a comparison table.

    python scripts/ab_score.py

Reads each run's deviations.jsonl and usage.json. "conf.FA" is a confident deviation that
matches no label (needs_review findings are counted separately). Cost uses the list price
below; update it if pricing or the model changes.
"""

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lab_vision.evaluation import evaluate, load_deviations, load_truth  # noqa: E402

FIXTURES = ROOT / "fixtures" / "lsv_mock_transformation"
USD_PER_M_INPUT, USD_PER_M_OUTPUT = 4.0, 20.0  # claude-opus-5-5 list price


def main() -> None:
    rows = []
    for run in sorted((ROOT / "runs" / "ab").glob("DJI-*")):
        if not (run / "summary.json").exists():
            continue  # still running
        _, number, arm, rep = run.name.split("-")
        clip = f"DJI-{number}"
        truth = load_truth(next(FIXTURES.glob(f"{clip}*.truth.json")))
        deviations = load_deviations(run / "deviations.jsonl")
        report = evaluate(truth, deviations)
        usage = json.loads((run / "usage.json").read_text())
        kinds = collections.Counter(d.kind.value for d in deviations)
        rows.append(
            {
                "clip": clip,
                "arm": arm,
                "rep": rep,
                "hit": f"{report.detected}/{report.expected}",
                "false_alarms": len(report.false_alarms),
                "review": len(report.review_flags),
                "unverified": kinds["unverified_check"],
                "skipped": kinds["skipped_step"],
                "incomplete": kinds["incomplete_step"],
                "unexpected": kinds["unexpected_event"],
                "cost": usage["input_tokens"] * USD_PER_M_INPUT / 1e6
                + usage["output_tokens"] * USD_PER_M_OUTPUT / 1e6,
                "served": ",".join(usage["served_models"]),
            }
        )

    print(
        f"{'clip':8} {'arm':4} {'rep':4} {'hit':>4} {'conf.FA':>7} {'review':>6} | "
        f"{'unverif':>7} {'skip':>4} {'incompl':>7} {'unexp':>5} | {'$':>5}  served"
    )
    for r in sorted(rows, key=lambda r: (r["clip"], r["arm"], r["rep"])):
        print(
            f"{r['clip']:8} {r['arm']:4} {r['rep']:4} {r['hit']:>4} {r['false_alarms']:7} "
            f"{r['review']:6} | {r['unverified']:7} {r['skipped']:4} {r['incomplete']:7} "
            f"{r['unexpected']:5} | {r['cost']:5.2f}  {r['served']}"
        )
    for arm in ("off", "on"):
        subset = [r for r in rows if r["arm"] == arm]
        if subset:
            print(f"arm {arm:3}: {len(subset)} runs, ${sum(r['cost'] for r in subset):.2f}")


if __name__ == "__main__":
    main()
