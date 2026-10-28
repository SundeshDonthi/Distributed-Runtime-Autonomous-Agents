"""Phase 8's standalone scheduler process: consumes workflow IDs off a global
`PendingWorkflowQueue` and runs each one to completion via
`run_workflow_async`, dispatching its tasks to independent worker processes
(`worker_mode="distributed"`) instead of running them in its own process the
way Phases 1-7's embedded scheduling did. This is what makes the API, the
scheduler, and the workers genuinely separate OS processes (Section 15J Phase
8), rather than `asyncio` coroutines sharing one process's memory -- the three
only ever communicate through Postgres and Redis, never a shared in-memory
object.

Multiple workflows run concurrently within this one process (bounded by
`--max-concurrent-workflows`), each on its own DB session -- `Session` objects
aren't safe to share across concurrent coroutines, and each workflow's own
round-by-round dispatch loop already handles its own internal task
concurrency, so this process's only job is to not run more *workflows* at
once than it's configured for.

Usage: python -m runtime.scheduler.scheduler_process [--max-concurrent-workflows N] [--poll-timeout SECONDS]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from runtime.scheduler.queue import PendingWorkflowQueue
from runtime.scheduler.scheduler import run_workflow_async
from runtime.state.db import get_session, init_db
from runtime.state.redis_client import get_redis

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT_WORKFLOWS = 8
DEFAULT_POLL_TIMEOUT_SECONDS = 1.0


async def _run_one(workflow_id: str) -> None:
    try:
        with get_session() as session:
            status = await run_workflow_async(session, workflow_id, worker_mode="distributed")
        logger.info("workflow %s finished: %s", workflow_id, status.value)
    except Exception:
        logger.exception("workflow %s raised while scheduling", workflow_id)


async def run(
    max_concurrent_workflows: int = DEFAULT_MAX_CONCURRENT_WORKFLOWS,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
) -> None:
    init_db()
    redis_client = get_redis()
    pending = PendingWorkflowQueue(redis_client)
    semaphore = asyncio.Semaphore(max_concurrent_workflows)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def _run_one_bounded(workflow_id: str) -> None:
        async with semaphore:
            await _run_one(workflow_id)

    in_flight: set[asyncio.Task] = set()
    logger.info("scheduler process started, polling for pending workflows")
    while not stop.is_set():
        workflow_id = await asyncio.to_thread(pending.pop, poll_timeout)
        if workflow_id is None:
            continue
        task = asyncio.create_task(_run_one_bounded(workflow_id))
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    if in_flight:
        await asyncio.wait(in_flight)
    logger.info("scheduler process shutting down")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-concurrent-workflows", type=int, default=DEFAULT_MAX_CONCURRENT_WORKFLOWS)
    parser.add_argument("--poll-timeout", type=float, default=DEFAULT_POLL_TIMEOUT_SECONDS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args.max_concurrent_workflows, args.poll_timeout))


if __name__ == "__main__":
    main()
