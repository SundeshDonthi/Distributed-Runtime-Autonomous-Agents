from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.recovery.retry import backoff_seconds, maybe_schedule_retry, reclaim_expired_lease
from runtime.state.orm import utcnow


def _failed_task(error_category: str | None, retry_count: int = 0, max_retries: int = 3) -> Task:
    return Task(
        id="t1",
        workflow_id="wf1",
        description="t",
        status=TaskStatus.FAILED,
        error="boom",
        error_category=error_category,
        retry_count=retry_count,
        max_retries=max_retries,
    )


def test_backoff_matches_section_5_9s_worked_example():
    # attempt 1 immediate, attempt 2 -> 1s, attempt 3 -> 2s, attempt 4 -> 4s
    assert backoff_seconds(0) == 0.0
    assert backoff_seconds(1) == 1.0
    assert backoff_seconds(2) == 2.0
    assert backoff_seconds(3) == 4.0


def test_permanent_failure_is_never_retried():
    task = _failed_task("permanent")
    maybe_schedule_retry(task)
    assert task.status == TaskStatus.FAILED
    assert task.retry_count == 0


def test_unclassified_failure_defaults_to_permanent():
    task = _failed_task(None)
    maybe_schedule_retry(task)
    assert task.status == TaskStatus.FAILED


def test_transient_failure_is_retried_with_backoff():
    task = _failed_task("transient", retry_count=0)
    before = utcnow()
    maybe_schedule_retry(task)

    assert task.status == TaskStatus.RETRYING
    assert task.retry_count == 1
    assert task.retry_after is not None
    assert task.retry_after >= before


def test_transient_failure_stops_retrying_once_max_retries_hit():
    task = _failed_task("transient", retry_count=3, max_retries=3)
    maybe_schedule_retry(task)
    assert task.status == TaskStatus.FAILED


def test_reclaim_expired_lease_goes_back_to_pending_and_clears_lease():
    task = Task(
        id="t2",
        workflow_id="wf1",
        description="t",
        status=TaskStatus.RUNNING,
        lease_owner="worker_1",
        fencing_token=1,
        retry_count=0,
        max_retries=3,
    )
    reclaim_expired_lease(task)

    assert task.status == TaskStatus.PENDING
    assert task.lease_owner is None
    assert task.lease_expires_at is None
    assert task.retry_count == 1
    assert task.error_category == "transient"


def test_reclaim_expired_lease_fails_once_retries_exhausted():
    task = Task(
        id="t3",
        workflow_id="wf1",
        description="t",
        status=TaskStatus.RUNNING,
        retry_count=3,
        max_retries=3,
    )
    reclaim_expired_lease(task)
    assert task.status == TaskStatus.FAILED
