from __future__ import annotations

import time

import redis

from runtime.models.task import Task
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository

DELAY_SECONDS = 0.2


def _delay_task(task_id: str, workflow_id: str, deps: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        workflow_id=workflow_id,
        description=task_id,
        dependencies=deps or [],
        input={"tool": "delay", "args": {"seconds": DELAY_SECONDS, "text": task_id}},
    )


def test_independent_tasks_run_concurrently(db_session):
    workflow_id = "wf_parallel"
    repository.create_workflow(db_session, workflow_id, "parallel objective")
    tasks = [_delay_task(f"t{i}", workflow_id) for i in range(4)]
    repository.save_tasks(db_session, tasks)

    sequential_estimate = DELAY_SECONDS * len(tasks)
    start = time.perf_counter()
    run_workflow(db_session, workflow_id, max_concurrency=len(tasks))
    elapsed = time.perf_counter() - start

    # 4 independent 0.2s tasks dispatched concurrently should take roughly
    # one delay's worth of wall time, not four -- a generous margin keeps this
    # from flaking on a loaded machine while still proving real overlap.
    assert elapsed < sequential_estimate * 0.6


def test_concurrency_limit_of_one_serializes_execution(db_session):
    workflow_id = "wf_serial_limit"
    repository.create_workflow(db_session, workflow_id, "serial objective")
    tasks = [_delay_task(f"t{i}", workflow_id) for i in range(3)]
    repository.save_tasks(db_session, tasks)

    sequential_estimate = DELAY_SECONDS * len(tasks)
    start = time.perf_counter()
    run_workflow(db_session, workflow_id, max_concurrency=1)
    elapsed = time.perf_counter() - start

    # With concurrency capped at 1, independent tasks must still run one at a
    # time -- proves the semaphore actually bounds dispatch, not just that
    # gather() is being called.
    assert elapsed >= sequential_estimate * 0.9


def test_dependency_ordering_still_enforced_under_concurrency(db_session):
    workflow_id = "wf_diamond_parallel"
    repository.create_workflow(db_session, workflow_id, "diamond objective")
    tasks = [
        _delay_task("a", workflow_id),
        _delay_task("b", workflow_id, deps=["a"]),
        _delay_task("c", workflow_id, deps=["a"]),
        _delay_task("d", workflow_id, deps=["b", "c"]),
    ]
    repository.save_tasks(db_session, tasks)

    run_workflow(db_session, workflow_id, max_concurrency=4)

    final = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert final["a"].completed_at <= final["b"].started_at
    assert final["a"].completed_at <= final["c"].started_at
    assert max(final["b"].completed_at, final["c"].completed_at) <= final["d"].started_at
    # b and c are independent siblings -- they should overlap, unlike a->b->d.
    assert final["b"].started_at < final["c"].completed_at
    assert final["c"].started_at < final["b"].completed_at


def test_falls_back_to_direct_dispatch_when_redis_unreachable(db_session):
    workflow_id = "wf_no_redis"
    repository.create_workflow(db_session, workflow_id, "no-redis objective")
    tasks = [_delay_task(f"t{i}", workflow_id) for i in range(3)]
    repository.save_tasks(db_session, tasks)

    unreachable_client = redis.Redis(
        host="127.0.0.1", port=1, socket_connect_timeout=0.1, decode_responses=True
    )

    sequential_estimate = DELAY_SECONDS * len(tasks)
    start = time.perf_counter()
    status = run_workflow(
        db_session, workflow_id, max_concurrency=len(tasks), redis_client=unreachable_client
    )
    elapsed = time.perf_counter() - start

    assert status == WorkflowStatus.COMPLETED
    assert all(
        t.output is not None for t in repository.get_tasks(db_session, workflow_id)
    )
    # Still genuinely concurrent -- the fallback path, not a silent hang/retry.
    assert elapsed < sequential_estimate * 0.6
