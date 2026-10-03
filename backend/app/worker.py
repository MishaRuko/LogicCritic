import asyncio

from app.config import get_settings
from app.services.extraction_jobs import (
    claim_next_extraction_job,
    process_extraction_job,
    recover_stale_extraction_jobs,
)


async def run() -> None:
    settings = get_settings()
    while True:
        await recover_stale_extraction_jobs()
        job_id = await claim_next_extraction_job()
        if job_id is None:
            await asyncio.sleep(settings.extraction_worker_poll_seconds)
            continue
        await process_extraction_job(job_id)


if __name__ == "__main__":
    asyncio.run(run())
