from __future__ import annotations

from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.dag import has_failed_dependency, is_workflow_done, ready_tasks, workflow_succeeded
from runtime.state import repository
from runtime.workers.worker import Worker


def run_workflow(session: Session, workflow_id: str, worker: Worker | None = None) -> WorkflowStatus:
    """Basic scheduler loop (Section 5.4): repeatedly find tasks whose
    dependencies are satisfied, dispatch them, and persist the result, until
    every task reaches a terminal state. Sequential and single-worker by
    design for Phase 1 -- Phase 2 adds concurrent dispatch across branches.
    """
    worker = worker or Worker()
    tasks = repository.get_tasks(session, workflow_id)

    while not is_workflow_done(tasks):
        by_id = {t.id: t for t in tasks}

        progressed = False
        for task in tasks:
            if task.status == TaskStatus.PENDING and has_failed_dependency(task, by_id):
                task.status = TaskStatus.CANCELLED
                repository.update_task(session, task)
                progressed = True

        for task in ready_tasks(tasks):
            worker.execute(task)
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
    return final_status
