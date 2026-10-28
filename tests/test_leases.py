from __future__ import annotations

from datetime import timedelta

from runtime.models.task import Task, TaskStatus
from runtime.scheduler.leases import (
    acquire_lease,
    fencing_token_is_current,
    find_expired_leases,
    is_lease_expired,
)
from runtime.state.orm import utcnow


def _task(status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(id="t1", workflow_id="wf1", description="t", status=status)


def test_acquire_lease_claims_and_increments_fencing_token():
    task = _task()
    acquire_lease(task, "worker_1", lease_seconds=30)

    assert task.status == TaskStatus.RUNNING
    assert task.lease_owner == "worker_1"
    assert task.fencing_token == 1
    assert task.lease_expires_at > utcnow()


def test_reacquiring_a_lease_increments_the_token_again():
    task = _task()
    acquire_lease(task, "worker_1", lease_seconds=30)
    acquire_lease(task, "worker_2", lease_seconds=30)

    assert task.fencing_token == 2
    assert task.lease_owner == "worker_2"


def test_is_lease_expired_false_with_no_lease():
    assert is_lease_expired(_task()) is False


def test_is_lease_expired_true_once_past_expiry():
    task = _task()
    task.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert is_lease_expired(task) is True


def test_is_lease_expired_false_before_expiry():
    task = _task()
    task.lease_expires_at = utcnow() + timedelta(seconds=30)
    assert is_lease_expired(task) is False


def test_find_expired_leases_only_matches_running_tasks_past_expiry():
    expired_running = _task(TaskStatus.RUNNING)
    expired_running.id = "expired_running"
    expired_running.lease_expires_at = utcnow() - timedelta(seconds=1)

    live_running = _task(TaskStatus.RUNNING)
    live_running.id = "live_running"
    live_running.lease_expires_at = utcnow() + timedelta(seconds=30)

    expired_but_completed = _task(TaskStatus.COMPLETED)
    expired_but_completed.id = "expired_completed"
    expired_but_completed.lease_expires_at = utcnow() - timedelta(seconds=1)

    result = find_expired_leases([expired_running, live_running, expired_but_completed])

    assert [t.id for t in result] == ["expired_running"]


def test_fencing_token_is_current_matches_matching_claim():
    task = _task()
    acquire_lease(task, "worker_1", lease_seconds=30)
    fresh_from_db = task.model_copy()

    assert fencing_token_is_current(task, fresh_from_db) is True


def test_fencing_token_is_current_rejects_a_stale_claim():
    # worker_1 claims the task (token=1), crashes, and the task is reclaimed
    # and re-claimed by worker_2 (token=2) before worker_1's stale in-memory
    # copy ever tries to commit its result.
    original = _task()
    acquire_lease(original, "worker_1", lease_seconds=30)
    stale_copy = original.model_copy()

    current = original.model_copy()
    acquire_lease(current, "worker_2", lease_seconds=30)

    assert fencing_token_is_current(stale_copy, current) is False
