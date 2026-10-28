"""Phase 8's own definition of done, exercised for real: a workflow's tasks
dispatched to a worker that is a genuinely separate OS process, communicating
with the scheduler only through Redis -- not an in-process `Worker` called as
an ordinary coroutine (Phases 1-7's "embedded" `worker_mode`, still the
default and still what every other test in this suite exercises).

The worker process itself never touches Postgres (see
`runtime.workers.worker_process`'s own docstring), so unlike
`tests/test_chaos_recovery.py` these tests only need a reachable Redis, not a
live Postgres -- `db_session`'s usual in-memory SQLite is enough for the
scheduler side.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
import redis as redis_lib

from runtime.models.task import Task
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.queue import DispatchQueue, ResultQueue
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.redis_client import get_redis, is_reachable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    not is_reachable(get_redis()),
    reason="distributed worker mode needs a reachable Redis -- it's the only "
    "channel a worker process and the scheduler share",
)


@pytest.fixture()
def worker_subprocess():
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "runtime.workers.worker_process",
            "--worker-id", "test_worker", "--poll-timeout", "0.2",
        ],
        cwd=REPO_ROOT,
    )
    try:
        time.sleep(0.5)  # let it bind to Redis before anything gets pushed to it
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_a_real_worker_process_executes_a_dispatched_tool_call():
    """The worker process in isolation, with no scheduler involved at all:
    push a request onto the global `DispatchQueue` by hand, start a real
    worker process, and check its reported result on the `ResultQueue`.
    """
    redis_client = get_redis()
    dispatch_queue = DispatchQueue(redis_client)
    result_queue = ResultQueue(redis_client, "wf_isolated")
    dispatch_queue.clear()
    result_queue.clear()

    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.workers.worker_process", "--poll-timeout", "0.2"],
        cwd=REPO_ROOT,
    )
    try:
        time.sleep(0.5)
        dispatch_queue.push("wf_isolated", "t1", "uppercase", {"text": "hi"})
        result = result_queue.pop(timeout=5.0)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result is not None
    assert result["task_id"] == "t1"
    assert result["status"] == "COMPLETED"
    assert result["output"] == {"result": "HI"}
    assert result["error"] is None


def test_run_workflow_with_distributed_worker_mode_runs_tasks_in_a_separate_process(
    db_session, worker_subprocess
):
    workflow_id = "wf_distributed_1"
    repository.create_workflow(db_session, workflow_id, "distributed objective")
    repository.save_tasks(
        db_session,
        [
            Task(id="t1", workflow_id=workflow_id, description="t1",
                 input={"tool": "echo", "args": {"text": "hi"}}),
            Task(id="t2", workflow_id=workflow_id, description="t2", dependencies=["t1"],
                 input={"tool": "uppercase", "args": {"text": "hi"}}),
        ],
    )

    status = run_workflow(db_session, workflow_id, worker_mode="distributed")

    # The only way this returns COMPLETED at all is if the subprocess
    # actually ran both tools and reported back over Redis -- nothing in
    # this process ever calls a tool function directly in distributed mode.
    assert status == WorkflowStatus.COMPLETED
    tasks = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert tasks["t1"].output == {"result": "hi"}
    assert tasks["t2"].output == {"result": "HI"}


def test_distributed_mode_still_runs_failed_tasks_through_the_usual_retry_path(
    db_session, worker_subprocess
):
    """A remote worker's failure is reported back exactly like an embedded
    one's would be -- the scheduler's retry/error-category logic (Phase 4)
    doesn't need to know or care that the failure happened in another
    process.
    """
    workflow_id = "wf_distributed_failure"
    repository.create_workflow(db_session, workflow_id, "distributed failure objective")
    repository.save_tasks(
        db_session,
        [Task(id="t1", workflow_id=workflow_id, description="t1",
              input={"tool": "does_not_exist", "args": {}})],
    )

    status = run_workflow(db_session, workflow_id, worker_mode="distributed")

    assert status == WorkflowStatus.FAILED
    task = repository.get_tasks(db_session, workflow_id)[0]
    assert task.error_category == "permanent"
    assert "unknown tool" in task.error


def test_distributed_mode_without_redis_raises_instead_of_silently_falling_back(db_session):
    repository.create_workflow(db_session, "wf_no_redis_distributed", "objective")
    repository.save_tasks(
        db_session,
        [Task(id="t1", workflow_id="wf_no_redis_distributed", description="t1",
              input={"tool": "echo", "args": {"text": "x"}})],
    )
    bad_client = redis_lib.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)

    with pytest.raises(RuntimeError, match="distributed"):
        run_workflow(
            db_session, "wf_no_redis_distributed", worker_mode="distributed", redis_client=bad_client
        )
