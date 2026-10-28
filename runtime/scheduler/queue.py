from __future__ import annotations

import json

import redis

_KEY_TMPL = "workflow:{workflow_id}:ready_queue"
_DISPATCH_KEY = "distributed:dispatch_queue"
_RESULT_KEY_TMPL = "workflow:{workflow_id}:distributed_results"
_PENDING_WORKFLOWS_KEY = "scheduler:pending_workflows"


class ReadyQueue:
    """A Redis-backed FIFO queue of task IDs that are READY to run (Section
    5.7: Redis holds task queues; Postgres stays the source of truth for task
    state -- Section 15F.2). This decouples "a task became ready" from "a
    worker picked it up", consumed here by `asyncio` worker loops inside the
    scheduler's own process (Phase 2's concurrency). `DispatchQueue`/
    `ResultQueue` below are Phase 8's counterpart for workers that are
    genuinely separate OS processes.
    """

    def __init__(self, client: redis.Redis, workflow_id: str) -> None:
        self._redis = client
        self._key = _KEY_TMPL.format(workflow_id=workflow_id)

    def push(self, task_id: str) -> None:
        self._redis.rpush(self._key, task_id)

    def pop(self, timeout: float = 0.1) -> str | None:
        result = self._redis.blpop([self._key], timeout=timeout)
        if result is None:
            return None
        _, task_id = result
        return task_id

    def clear(self) -> None:
        self._redis.delete(self._key)


class DispatchQueue:
    """Phase 8's cross-process counterpart to `ReadyQueue`: a single global
    queue of task-execution *requests*, consumed by independent worker OS
    processes (`runtime.workers.worker_process`) rather than by `asyncio`
    coroutines sharing the scheduler's own process memory. Global, not keyed
    by workflow like `ReadyQueue` -- a worker process doesn't belong to any
    particular workflow, so there's nothing to key it by; which workflow a
    request belongs to travels inside the payload instead, so a worker can
    address its result back to the right place.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client
        self._key = _DISPATCH_KEY

    def push(self, workflow_id: str, task_id: str, tool: str | None, args: dict) -> None:
        payload = json.dumps(
            {"workflow_id": workflow_id, "task_id": task_id, "tool": tool, "args": args}
        )
        self._redis.rpush(self._key, payload)

    def pop(self, timeout: float = 1.0) -> dict | None:
        result = self._redis.blpop([self._key], timeout=timeout)
        if result is None:
            return None
        _, payload = result
        return json.loads(payload)

    def clear(self) -> None:
        self._redis.delete(self._key)


class ResultQueue:
    """The reply side of `DispatchQueue`: a worker process pushes a task's
    outcome here once it's finished running it, keyed by workflow so the
    scheduler process handling that workflow only ever waits on results that
    are actually its own.
    """

    def __init__(self, client: redis.Redis, workflow_id: str) -> None:
        self._redis = client
        self._key = _RESULT_KEY_TMPL.format(workflow_id=workflow_id)

    def push(self, result: dict) -> None:
        self._redis.rpush(self._key, json.dumps(result))

    def pop(self, timeout: float = 1.0) -> dict | None:
        result = self._redis.blpop([self._key], timeout=timeout)
        if result is None:
            return None
        _, payload = result
        return json.loads(payload)

    def clear(self) -> None:
        self._redis.delete(self._key)


class PendingWorkflowQueue:
    """Phase 8's hand-off from the API process to the standalone scheduler
    process (`runtime.scheduler.scheduler_process`): `POST /workflows` pushes
    a workflow id here instead of running it inline via FastAPI
    `BackgroundTasks` the way Phases 1-7's embedded scheduling does (see
    `SCHEDULING_MODE` in `runtime/api/routes.py`). Global, not per-workflow --
    any scheduler process instance may pick up any pending workflow.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client
        self._key = _PENDING_WORKFLOWS_KEY

    def push(self, workflow_id: str) -> None:
        self._redis.rpush(self._key, workflow_id)

    def pop(self, timeout: float = 1.0) -> str | None:
        result = self._redis.blpop([self._key], timeout=timeout)
        if result is None:
            return None
        _, workflow_id = result
        return workflow_id

    def clear(self) -> None:
        self._redis.delete(self._key)
