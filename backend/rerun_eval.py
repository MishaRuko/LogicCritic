"""One-off: rebuild the pilot's cases on freshly fetched, structured packets and run them."""

import asyncio
import sys
import uuid

from sqlalchemy import select

from app.database import session_factory
from app.evaluations.europe_pmc import fetch_packet
from app.evaluations.service import create_evaluation, render_report, run_evaluation
from app.models import EvaluationCase

OLD = uuid.UUID("d332f94d-8a1e-40a3-bc03-3eb2a695aabc")


async def main(name: str, positions: list[int] | None) -> None:
    async with session_factory() as session:
        old = list(
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.evaluation_run_id == OLD)
                .order_by(EvaluationCase.position)
            )
        )
    if positions:
        old = [case for case in old if case.position in positions]
    packets = {}
    for case in old:
        pmcid = case.packet["id"]
        if pmcid not in packets:
            packets[pmcid] = await fetch_packet(pmcid)
    cases = [{"packet": packets[case.packet["id"]], **case.rubric} for case in old]
    evaluation_id = await create_evaluation(name, cases)
    print("evaluation", evaluation_id, flush=True)
    await run_evaluation(evaluation_id)
    print(await render_report(evaluation_id))


if __name__ == "__main__":
    picked = [int(item) for item in sys.argv[2].split(",")] if len(sys.argv) > 2 else None
    asyncio.run(main(sys.argv[1], picked))
