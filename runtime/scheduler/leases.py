from __future__ import annotations

from datetime import datetime, timedelta

from runtime.models.task import Task, TaskStatus
from runtime.state.orm import utcnow

DEFAULT_LEASE_SECONDS = 30.0


def acquire_lease(task: Task, worker_id: str, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
    """Claims a task for execution (Section 12's "Worker acquires Task A").
    Mutates the task in place: moves it to RUNNING, stamps a lease owner and
    expiry, and increments the fencing token (Section 15F.4) -- a later
    commit is only valid if the fencing token it was issued with still
    matches what's in the database (see `fencing_token_is_current`).
    """
    task.status = TaskStatus.RUNNING
    task.lease_owner = worker_id
    task.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    task.fencing_token += 1


def is_lease_expired(task: Task, now: datetime | None = None) -> bool:
    if task.lease_expires_at is None:
        return False
    return (now or utcnow()) >= task.lease_expires_at


def find_expired_leases(tasks: list[Task], now: datetime | None = None) -> list[Task]:
    """RUNNING tasks whose owning worker never reported back before its lease
    expired (Section 13: "the scheduler considers the worker dead and
    eventually reassigns its leased task"). In this single-process runtime
    this mainly catches a task left RUNNING by a *previous, crashed*
    invocation of the scheduler -- true concurrent multi-process ownership
    conflicts are Phase 8's territory, but the same mechanism covers both.
    """
    now = now or utcnow()
    return [t for t in tasks if t.status == TaskStatus.RUNNING and is_lease_expired(t, now)]


def fencing_token_is_current(task_in_memory: Task, task_in_db: Task) -> bool:
    """Section 15F.4: "A worker may commit task completion only when its
    token and lease ownership still match the current record. Expired
    workers must not overwrite newer attempt results." `task_in_memory` is
    the task object the worker was handed when it acquired its lease;
    `task_in_db` is a fresh read of the same row. If another claim has since
    incremented the token (because this one's lease was reclaimed as
    expired), this returns False and the caller must discard its result
    rather than commit it.
    """
    return (
        task_in_memory.fencing_token == task_in_db.fencing_token
        and task_in_memory.lease_owner == task_in_db.lease_owner
    )
