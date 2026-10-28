from __future__ import annotations

import asyncio
import random
from datetime import timedelta

import redis as redis_lib
from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.routing.dispatch import record_outcome, resolve_task_tool
from runtime.routing.heuristic import DEFAULT_WEIGHTS, RouterWeights
from runtime.scheduler import idempotency, leases, retry
from runtime.scheduler.dag import (
    has_failed_dependency,
    is_workflow_done,
    next_retry_wakeup,
    ready_tasks,
    workflow_succeeded,
)
from runtime.scheduler.queue import ReadyQueue
from runtime.state import repository
from runtime.state.orm import utcnow
from runtime.state.redis_client import get_redis, is_reachable
from runtime.workers.worker import Worker

DEFAULT_MAX_CONCURRENCY = 8
MAX_POLL_SECONDS = 5.0


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


async def _heartbeat_loop(
    session: Session, dispatched: list[Task], interval: float, lease_seconds: float, stop: asyncio.Event
) -> None:
    """Section 13: while a task is genuinely still running, periodically
    extend its lease so a slow-but-healthy task isn't mistaken for an
    orphaned one and wrongly reclaimed. Runs as the single other consumer of
    `session` while dispatch is in flight -- the main coroutine doesn't touch
    `session` again until this loop has been stopped and awaited, so there's
    no concurrent access to guard against.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            return
        renewed = False
        for task in dispatched:
            if task.status == TaskStatus.RUNNING:
                task.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
                repository.update_task(session, task)
                renewed = True
        if renewed:
            session.commit()


def _event_payload(task: Task) -> dict:
    return {
        "task_id": task.id,
        "status": task.status.value,
        "error": task.error,
        "retry_count": task.retry_count,
    }


async def run_workflow_async(
    session: Session,
    workflow_id: str,
    worker: Worker | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    redis_client: redis_lib.Redis | None = None,
    routing_weights: RouterWeights = DEFAULT_WEIGHTS,
    explore_probability: float = 0.0,
    rng: random.Random | None = None,
    worker_id: str = "worker_1",
    lease_seconds: float = leases.DEFAULT_LEASE_SECONDS,
    heartbeat_interval: float | None = None,
) -> WorkflowStatus:
    """The scheduler loop (Phases 2-4). Each round's READY tasks are pushed
    onto a Redis-backed queue (Section 5.7) and pulled by up to
    `max_concurrency` worker loops; falls back to direct dispatch if Redis is
    unreachable (Section 15A.1). On top of Phase 2's concurrency, this adds
    Phase 4's reliability layer:

    - every meaningful state change is committed immediately, not deferred to
      one commit at the very end of the whole workflow (Section 5.8's
      checkpointing: a crash should only cost the work genuinely in flight at
      that moment, not everything done so far);
    - a task is claimed with a lease + fencing token before it's dispatched,
      and a stale claim (Section 15F.4) is refused at commit time;
    - a lease that expired before its owner reported back is reclaimed
      (Section 13) at the start of every round;
    - a long-running task's lease is renewed by a heartbeat while it's
      genuinely still in flight, so it isn't wrongly reclaimed;
    - a task result is checked against the idempotency ledger before
      re-dispatching, and recorded there alongside the task row in the same
      commit (Section 15F.3);
    - a failed task is classified transient/permanent and either retried with
      exponential backoff or given up on (Section 5.9);
    - every transition also writes an outbox event in the same commit
      (Section 15F.4/15G.2).
    """
    worker = worker or Worker()
    redis_client = redis_client or get_redis()
    queue = ReadyQueue(redis_client, workflow_id) if is_reachable(redis_client) else None
    if queue:
        queue.clear()
    heartbeat_interval = heartbeat_interval if heartbeat_interval is not None else lease_seconds / 3

    tasks = repository.get_tasks(session, workflow_id)

    while not is_workflow_done(tasks):
        by_id = {t.id: t for t in tasks}
        progressed = False

        # Reclaim tasks orphaned by a worker that never reported back before
        # its lease expired (Section 13) -- checked first, every round, so a
        # previous crashed run's stale claims don't block anything forever.
        expired = leases.find_expired_leases(tasks)
        for task in expired:
            retry.reclaim_expired_lease(task)
            repository.update_task(session, task)
            repository.record_outbox_event(session, task.id, "task.lease_reclaimed", _event_payload(task))
            progressed = True
        if expired:
            session.commit()

        for task in tasks:
            if task.status == TaskStatus.PENDING and has_failed_dependency(task, by_id):
                task.status = TaskStatus.CANCELLED
                repository.update_task(session, task)
                repository.record_outbox_event(session, task.id, "task.cancelled", _event_payload(task))
                progressed = True
        if progressed:
            session.commit()

        round_tasks = ready_tasks(tasks)
        if round_tasks:
            # Idempotency short-circuit (Section 15F.3), checked first: a
            # task whose logical operation already completed on a prior
            # attempt is marked COMPLETED directly from the cached result --
            # it never goes near routing, a lease, or a worker, because none
            # of that should run again for a tool call that already happened.
            cached_ids: set[str] = set()
            to_process: list[Task] = []
            for task in round_tasks:
                cached = idempotency.check_cached_result(session, task)
                if cached is not None:
                    task.status = TaskStatus.COMPLETED
                    task.output = cached
                    task.started_at = task.started_at or utcnow()
                    task.completed_at = utcnow()
                    cached_ids.add(task.id)
                else:
                    to_process.append(task)

            # Routing (Section 15A.1), only for tasks that will actually be
            # dispatched: a capability-addressed task is resolved to a
            # concrete tool before anyone acts on it, and the decision is
            # durable before dispatch.
            decision_ids = {
                task.id: decision_id
                for task in to_process
                if (
                    decision_id := resolve_task_tool(
                        session, task, routing_weights, explore_probability, rng
                    )
                )
                is not None
            }

            dispatched: list[Task] = []
            for task in to_process:
                leases.acquire_lease(task, worker_id, lease_seconds)
                idempotency.mark_in_progress(session, task)
                dispatched.append(task)

            # Plain `update_task`, not the fencing-checked commit: this is
            # the claim itself (establishing a brand-new fencing token), not
            # a report of work done under a token that was already granted --
            # a fresh token can never match the row's prior one, so the
            # fencing check would reject every claim by construction.
            for task in round_tasks:
                repository.update_task(session, task)
            session.commit()  # cache hits, leases, and in-progress markers all durable before dispatch

            if dispatched:
                stop_heartbeat = asyncio.Event()
                heartbeat = asyncio.create_task(
                    _heartbeat_loop(session, dispatched, heartbeat_interval, lease_seconds, stop_heartbeat)
                )
                try:
                    if queue is not None:
                        await _run_round_via_queue(dispatched, queue, worker, max_concurrency)
                    else:
                        await _run_round_directly(dispatched, worker, max_concurrency)
                finally:
                    stop_heartbeat.set()
                    await heartbeat

            dispatched_ids = {t.id for t in dispatched}
            for task in round_tasks:
                if task.id in decision_ids:
                    record_outcome(session, task, decision_ids[task.id])
                if task.id in dispatched_ids:
                    if task.status == TaskStatus.FAILED:
                        retry.maybe_schedule_retry(task)
                    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                        idempotency.record_result(session, task)
                applied = repository.commit_task_if_fencing_valid(session, task)
                if applied:
                    event_type = (
                        "task.cache_hit" if task.id in cached_ids else f"task.{task.status.value.lower()}"
                    )
                    repository.record_outbox_event(session, task.id, event_type, _event_payload(task))
            session.commit()
            progressed = True

        if not progressed:
            wakeup = next_retry_wakeup(tasks)
            if wakeup is not None:
                delay = min(max(0.0, (wakeup - utcnow()).total_seconds()), MAX_POLL_SECONDS)
                await asyncio.sleep(delay)
            else:
                # No ready tasks, nothing retrying with a future wake time,
                # and nothing failed upstream this round: a bug in the plan
                # (e.g. an orphaned dependency), not a transient condition.
                for task in tasks:
                    if task.status == TaskStatus.PENDING:
                        task.status = TaskStatus.BLOCKED
                        repository.update_task(session, task)
                session.commit()
                break

        tasks = repository.get_tasks(session, workflow_id)

    final_status = (
        WorkflowStatus.COMPLETED if workflow_succeeded(tasks) else WorkflowStatus.FAILED
    )
    repository.update_workflow_status(session, workflow_id, final_status, completed=True)
    session.commit()
    if queue:
        queue.clear()
    return final_status


def run_workflow(
    session: Session,
    workflow_id: str,
    worker: Worker | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    redis_client: redis_lib.Redis | None = None,
    routing_weights: RouterWeights = DEFAULT_WEIGHTS,
    explore_probability: float = 0.0,
    rng: random.Random | None = None,
    worker_id: str = "worker_1",
    lease_seconds: float = leases.DEFAULT_LEASE_SECONDS,
    heartbeat_interval: float | None = None,
) -> WorkflowStatus:
    """Sync wrapper over `run_workflow_async` for callers (the FastAPI
    background task, the existing test suite) that aren't already inside an
    event loop.
    """
    return asyncio.run(
        run_workflow_async(
            session,
            workflow_id,
            worker,
            max_concurrency,
            redis_client,
            routing_weights,
            explore_probability,
            rng,
            worker_id,
            lease_seconds,
            heartbeat_interval,
        )
    )
