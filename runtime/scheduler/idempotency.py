from __future__ import annotations

from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.state import repository

__all__ = ["check_cached_result", "mark_in_progress", "record_result"]


def check_cached_result(session: Session, task: Task) -> dict | None:
    """If this task's logical operation already completed successfully on a
    prior attempt, returns the cached result so the caller can skip
    re-dispatching it entirely -- the ledger half of Section 15F.3's
    idempotency story (keyed by `workflow_id:task_id`, not attempt number).
    Returns None for a first attempt, or for a prior attempt that never
    resolved -- Section 15F.3's "reconciliation for ambiguous outcomes": we
    don't actually know whether that attempt's external call went through,
    so the honest choice is to let it be retried rather than silently
    assume either outcome.
    """
    entry = repository.get_idempotency_entry(session, task.idempotency_key)
    if entry is not None and entry.status == "COMPLETED":
        return entry.result
    return None


def mark_in_progress(session: Session, task: Task) -> None:
    """Recorded before dispatch, in the same commit as the lease acquisition
    (Section 12). If the process crashes between the external call actually
    succeeding and the task row being updated, this IN_PROGRESS row is the
    visible trace of that ambiguous attempt -- not a fix for it (only a
    provider-side idempotency key can make that race itself safe, Section
    15K), but an honest record that it happened rather than silence.
    """
    repository.upsert_idempotency_entry(
        session=session,
        idempotency_key=task.idempotency_key,
        workflow_id=task.workflow_id,
        task_id=task.id,
        status="IN_PROGRESS",
    )


def record_result(session: Session, task: Task) -> None:
    """Called after a task finishes, in the same commit as the task row
    update, so the ledger and the task state can never diverge because a
    crash landed between two separate commits.
    """
    status = "COMPLETED" if task.status == TaskStatus.COMPLETED else "FAILED"
    repository.upsert_idempotency_entry(
        session=session,
        idempotency_key=task.idempotency_key,
        workflow_id=task.workflow_id,
        task_id=task.id,
        status=status,
        result=task.output,
    )
