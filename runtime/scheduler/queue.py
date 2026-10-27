from __future__ import annotations

import redis

_KEY_TMPL = "workflow:{workflow_id}:ready_queue"


class ReadyQueue:
    """A Redis-backed FIFO queue of task IDs that are READY to run (Section
    5.7: Redis holds task queues; Postgres stays the source of truth for task
    state -- Section 15F.2). This decouples "a task became ready" from "a
    worker picked it up", which is what distributing workers across separate
    processes (Phase 6) will need; here it's still consumed within one
    process.
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
