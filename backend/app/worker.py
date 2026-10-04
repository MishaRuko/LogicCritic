import asyncio

from app.agent.loop import execute_run
from app.agent.runs import claim_next_agent_run, recover_stale_agent_runs
from app.config import get_settings
from app.services.extraction_jobs import (
    claim_next_extraction_job,
    process_extraction_job,
    recover_stale_extraction_jobs,
)


async def run_extraction_loop() -> None:
    settings = get_settings()
    while True:
        await recover_stale_extraction_jobs()
        job_id = await claim_next_extraction_job()
        if job_id is None:
            await asyncio.sleep(settings.extraction_worker_poll_seconds)
            continue
        await process_extraction_job(job_id)


async def run_agent_loop() -> None:
    """Agent runs take minutes, so they get their own loop and never block extraction."""
    settings = get_settings()
    while True:
        await recover_stale_agent_runs()
        run_id = await claim_next_agent_run()
        if run_id is None:
            await asyncio.sleep(settings.extraction_worker_poll_seconds)
            continue
        await execute_run(run_id)


async def run() -> None:
    await asyncio.gather(run_extraction_loop(), run_agent_loop())


if __name__ == "__main__":
    asyncio.run(run())
