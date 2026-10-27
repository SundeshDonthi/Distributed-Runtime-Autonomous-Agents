from __future__ import annotations

import asyncio

import redis as redis_lib
from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.dag import has_failed_dependency, is_workflow_done, ready_tasks, workflow_succeeded
from runtime.scheduler.queue import ReadyQueue
from runtime.state import repository
from runtime.state.redis_client import get_redis, is_reachable
from runtime.workers.worker import Worker

DEFAULT_MAX_CONCURRENCY = 8


async def _run_round_via_queue(
    round_tasks: list[Task], queue: ReadyQueue, worker: Worker, max_concurrency: int
) -> None:
    for task in round_tasks:
        queue.push(task.id)
    by_id = {task.id: task for task in round_tasks}

    async def worker_loop() -> None:
        while True:
            task_id = await asyncio.to_thread(queue.pop)
            if task_id is None:
                return
            await worker.execute_async(by_id[task_id])

    workers = min(max_concurrency, len(round_tasks))
    await asyncio.gather(*(worker_loop() for _ in range(workers)))


async def _run_round_directly(
    round_tasks: list[Task], worker: Worker, max_concurrency: int
) -> None:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def dispatch(task: Task) -> Task:
        async with semaphore:
            return await worker.execute_async(task)

    await asyncio.gather(*(dispatch(t) for t in round_tasks))


async def run_workflow_async(
    session: Session,
    workflow_id: str,
    worker: Worker | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    redis_client: redis_lib.Redis | None = None,
) -> WorkflowStatus:
    """Phase 2 scheduler loop. Each round's READY tasks are pushed onto a
    Redis-backed queue (Section 5.7) and pulled by up to `max_concurrency`
    worker loops, instead of being dispatched directly -- the same decoupling
    Phase 6's distributed workers will rely on, just within one process for
    now. If Redis is unreachable, falls back to direct concurrent dispatch
    (Section 15A.1's "optional at runtime, fall back to the deterministic
    baseline" philosophy) so the scheduler still works without it.
    """
    worker = worker or Worker()
    redis_client = redis_client or get_redis()
    queue = ReadyQueue(redis_client, workflow_id) if is_reachable(redis_client) else None
    if queue:
        queue.clear()

    tasks = repository.get_tasks(session, workflow_id)

    while not is_workflow_done(tasks):
        by_id = {t.id: t for t in tasks}

        progressed = False
        for task in tasks:
            if task.status == TaskStatus.PENDING and has_failed_dependency(task, by_id):
                task.status = TaskStatus.CANCELLED
                repository.update_task(session, task)
                progressed = True

        round_tasks = ready_tasks(tasks)
        if round_tasks:
            if queue is not None:
                await _run_round_via_queue(round_tasks, queue, worker, max_concurrency)
            else:
                await _run_round_directly(round_tasks, worker, max_concurrency)
            for task in round_tasks:
                repository.update_task(session, task)
            progressed = True

        if not progressed:
            # No ready tasks and nothing failed upstream: a bug in the plan
            # (e.g. an orphaned dependency) rather than a transient condition.
            for task in tasks:
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.BLOCKED
                    repository.update_task(session, task)
            break

        tasks = repository.get_tasks(session, workflow_id)

    final_status = (
        WorkflowStatus.COMPLETED if workflow_succeeded(tasks) else WorkflowStatus.FAILED
    )
    repository.update_workflow_status(session, workflow_id, final_status, completed=True)
    if queue:
        queue.clear()
    return final_status


def run_workflow(
    session: Session,
    workflow_id: str,
    worker: Worker | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    redis_client: redis_lib.Redis | None = None,
) -> WorkflowStatus:
    """Sync wrapper over `run_workflow_async` for callers (the FastAPI
    background task, the existing test suite) that aren't already inside an
    event loop.
    """
    return asyncio.run(
        run_workflow_async(session, workflow_id, worker, max_concurrency, redis_client)
    )
