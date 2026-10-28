"""Phase 8's genuinely distributed worker: a standalone OS process (launched
by Docker Compose, or directly via `python -m runtime.workers.worker_process`)
that pulls task-execution requests off the global `DispatchQueue` and reports
results onto the requesting workflow's `ResultQueue`. Unlike Phases 2-7's
in-process `Worker`, which the scheduler calls as an ordinary coroutine inside
its own event loop, this process shares nothing with the scheduler but Redis
-- killing it, or running many of them (`docker compose up --scale
worker=N`), changes nothing about correctness, only throughput (Section 5.5:
a worker is a stateless, horizontally scalable executor).

A worker process never touches task state in Postgres -- lease ownership,
fencing, retry/replan decisions, and idempotency bookkeeping all stay exactly
where Phases 4/5/7 put them, in the scheduler process. This process only ever
runs an already-resolved `{"tool": ..., "args": ...}` request and reports what
happened, reusing the same `Worker.execute_async` that Phases 1-7 already run
in-process -- see `runtime.workers.worker.Worker`'s own docstring for why
routing and persistence are deliberately kept out of it.

Usage: python -m runtime.workers.worker_process [--worker-id ID] [--poll-timeout SECONDS]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from runtime.models.task import Task
from runtime.scheduler.queue import DispatchQueue, ResultQueue
from runtime.state.redis_client import get_redis
from runtime.workers.worker import Worker

logger = logging.getLogger(__name__)

DEFAULT_POLL_TIMEOUT_SECONDS = 1.0


def _result_payload(task: Task) -> dict:
    return {
        "task_id": task.id,
        "status": task.status.value,
        "output": task.output,
        "error": task.error,
        "error_category": task.error_category,
    }


async def run(worker_id: str = "worker_1", poll_timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS) -> None:
    redis_client = get_redis()
    dispatch_queue = DispatchQueue(redis_client)
    worker = Worker(worker_id)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("worker %s started, polling the distributed dispatch queue", worker_id)
    while not stop.is_set():
        request = await asyncio.to_thread(dispatch_queue.pop, poll_timeout)
        if request is None:
            continue
        task = Task(
            id=request["task_id"],
            workflow_id=request["workflow_id"],
            description="",
            input={"tool": request["tool"], "args": request["args"]},
        )
        task = await worker.execute_async(task)
        result_queue = ResultQueue(redis_client, request["workflow_id"])
        await asyncio.to_thread(result_queue.push, _result_payload(task))
        logger.info("worker %s ran task %s -> %s", worker_id, task.id, task.status.value)

    logger.info("worker %s shutting down", worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", default="worker_1")
    parser.add_argument("--poll-timeout", type=float, default=DEFAULT_POLL_TIMEOUT_SECONDS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args.worker_id, args.poll_timeout))


if __name__ == "__main__":
    main()
