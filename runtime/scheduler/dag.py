from __future__ import annotations

from runtime.models.task import TERMINAL_STATUSES, Task, TaskStatus


def ready_tasks(tasks: list[Task]) -> list[Task]:
    """A task becomes READY when every dependency has COMPLETED (Section 5.3)."""
    completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}
    return [
        t
        for t in tasks
        if t.status == TaskStatus.PENDING
        and all(dep in completed_ids for dep in t.dependencies)
    ]


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
