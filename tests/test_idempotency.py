from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.scheduler.idempotency import check_cached_result, mark_in_progress, record_result
from runtime.state import repository


def _task(session, task_id: str) -> Task:
    workflow_id = f"wf_{task_id}"
    repository.create_workflow(session, workflow_id, "idempotency test")
    task = Task(id=task_id, workflow_id=workflow_id, description="t")
    repository.save_tasks(session, [task])
    return task


def test_cold_start_has_no_cached_result(db_session):
    task = _task(db_session, "t1")
    assert check_cached_result(db_session, task) is None


def test_in_progress_entry_is_not_treated_as_cached(db_session):
    # Section 15F.3's "ambiguous outcome": an attempt that started but never
    # resolved must not be silently treated as either success or failure.
    task = _task(db_session, "t2")
    mark_in_progress(db_session, task)
    assert check_cached_result(db_session, task) is None


def test_completed_result_is_cached_and_reused(db_session):
    task = _task(db_session, "t3")
    mark_in_progress(db_session, task)
    task.status = TaskStatus.COMPLETED
    task.output = {"result": "x"}
    record_result(db_session, task)

    cached = check_cached_result(db_session, task)
    assert cached == {"result": "x"}


def test_failed_result_is_not_cached_as_a_success(db_session):
    task = _task(db_session, "t4")
    mark_in_progress(db_session, task)
    task.status = TaskStatus.FAILED
    record_result(db_session, task)

    assert check_cached_result(db_session, task) is None


def test_different_tasks_have_independent_cache_entries(db_session):
    task_a = _task(db_session, "a")
    task_b = _task(db_session, "b")
    task_a.status = TaskStatus.COMPLETED
    task_a.output = {"result": "a-result"}
    record_result(db_session, task_a)

    assert check_cached_result(db_session, task_a) == {"result": "a-result"}
    assert check_cached_result(db_session, task_b) is None
