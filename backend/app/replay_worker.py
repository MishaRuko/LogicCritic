"""Fast replays and samples stay responsive while a recording is being analysed."""

import asyncio

from app.worker import run_experiment_loop

if __name__ == "__main__":
    asyncio.run(run_experiment_loop(("demo", "replay")))
