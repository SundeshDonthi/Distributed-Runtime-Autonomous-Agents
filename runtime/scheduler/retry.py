from __future__ import annotations

from datetime import timedelta

from runtime.models.task import Task, TaskStatus
from runtime.state.orm import utcnow


def backoff_seconds(retry_count: int) -> float:
    """Section 5.9's example: attempt 1 immediate, attempt 2 -> 1s, attempt 3
    -> 2s, attempt 4 -> 4s. `retry_count` is "how many retries have been
    scheduled so far" (1 right after the first failure), so this doubles
    starting from 1s.
    """
    if retry_count <= 0:
        return 0.0
    return float(2 ** (retry_count - 1))


def maybe_schedule_retry(task: Task) -> None:
    """Called by the scheduler after a task comes back FAILED from the
    worker. Section 5.9's classification: `error_category` "permanent" (bad
    schema, unknown tool/capability -- retrying can't fix a request that was
    invalid to begin with) is left FAILED; "transient" (the tool's own call
    didn't go through -- Section 5.9 lists timeouts, rate limits, network
    failures) is retried with exponential backoff until `max_retries` is
    exhausted. A task with no error_category recorded is treated as
    permanent, the conservative default -- being unsure should never produce
    unbounded automatic retries of something we don't understand.
    """
    if task.status != TaskStatus.FAILED:
        return
    if task.error_category != "transient":
        return
    if task.retry_count >= task.max_retries:
        return

    task.retry_count += 1
    task.status = TaskStatus.RETRYING
    task.retry_after = utcnow() + timedelta(seconds=backoff_seconds(task.retry_count))


def reclaim_expired_lease(task: Task) -> None:
    """Section 13: "the scheduler considers the worker dead and eventually
    reassigns its leased task." Goes straight back to PENDING rather than
    through RETRYING's backoff -- the *tool* isn't believed to be flaky here,
    the process that was supposed to be running it just never reported back,
    so there's no reason to delay reassignment. Still counts against
    `max_retries` so a task that can genuinely never complete doesn't
    respawn forever.
    """
    task.error = "lease expired; owning worker presumed dead"
    task.error_category = "transient"
    task.lease_owner = None
    task.lease_expires_at = None

    if task.retry_count >= task.max_retries:
        task.status = TaskStatus.FAILED
    else:
        task.retry_count += 1
        task.status = TaskStatus.PENDING
