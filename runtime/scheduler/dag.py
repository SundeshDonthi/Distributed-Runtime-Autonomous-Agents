from __future__ import annotations

from datetime import datetime

from runtime.models.task import TERMINAL_STATUSES, Task, TaskStatus
from runtime.state.orm import utcnow


def ready_tasks(tasks: list[Task], now: datetime | None = None) -> list[Task]:
    """A task becomes READY when every dependency has COMPLETED (Section
    5.3). A RETRYING task (Section 5.9's backoff) is also READY once its
    `retry_after` has elapsed -- its dependencies were already satisfied the
    first time it ran, and nothing un-completes a dependency, so there's
    nothing left to recheck there.
    """
    now = now or utcnow()
    completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}

    def dependencies_met(t: Task) -> bool:
        return all(dep in completed_ids for dep in t.dependencies)

    return [
        t
        for t in tasks
        if (
            (t.status == TaskStatus.PENDING and dependencies_met(t))
            or (t.status == TaskStatus.RETRYING and t.retry_after is not None and t.retry_after <= now)
        )
    ]


def next_retry_wakeup(tasks: list[Task], now: datetime | None = None) -> datetime | None:
    """Earliest `retry_after` among RETRYING tasks that haven't elapsed yet --
    how long the scheduler should sleep, rather than busy-polling, when
    nothing is immediately ready but something will be soon.
    """
    now = now or utcnow()
    pending_backoffs = [
        t.retry_after for t in tasks if t.status == TaskStatus.RETRYING and t.retry_after and t.retry_after > now
    ]
    return min(pending_backoffs) if pending_backoffs else None


def has_failed_dependency(task: Task, tasks_by_id: dict[str, Task]) -> bool:
    return any(
        tasks_by_id[dep].status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
        for dep in task.dependencies
        if dep in tasks_by_id
    )


def is_workflow_done(tasks: list[Task]) -> bool:
    return all(t.status in TERMINAL_STATUSES for t in tasks)


def workflow_succeeded(tasks: list[Task]) -> bool:
    return all(t.status == TaskStatus.COMPLETED for t in tasks)
