"""Phase 8's actual end-to-end claim: a workflow running through three
genuinely separate OS processes -- a `scheduler_process` that only ever picks
workflow ids off Redis and a `worker_process` that only ever runs tools off
Redis -- coordinating purely through Postgres and Redis, never a shared
in-memory object. This is the real version of what every other test in this
project does inside a single process via `run_workflow`.

Needs a live Postgres (the three processes have to actually share durable
state) and a reachable Redis (the only channel they talk over), same
constraint as `tests/test_chaos_recovery.py`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from runtime.models.task import Task
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.queue import PendingWorkflowQueue
from runtime.state import repository
from runtime.state.db import DATABASE_URL, engine, get_session
from runtime.state.orm import Base
from runtime.state.redis_client import get_redis, is_reachable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _postgres_reachable() -> bool:
    if not DATABASE_URL.startswith("postgresql"):
        return False
    try:
        with engine.connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_postgres_reachable() and is_reachable(get_redis())),
    reason="a real multi-process run needs both a live Postgres (shared "
    "durable state) and a reachable Redis (the only channel the processes "
    "talk over)",
)


@pytest.fixture()
def distributed_cluster():
    """One scheduler process and one worker process, both genuinely separate
    OS processes, both talking only to Postgres/Redis -- never to each other
    directly or to this test process.
    """
    scheduler_proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.scheduler.scheduler_process", "--poll-timeout", "0.2"],
        cwd=REPO_ROOT,
    )
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.workers.worker_process", "--poll-timeout", "0.2"],
        cwd=REPO_ROOT,
    )
    try:
        time.sleep(1.0)  # let both bind to Redis/Postgres before anything is enqueued
        yield
    finally:
        for proc in (scheduler_proc, worker_proc):
            proc.terminate()
        for proc in (scheduler_proc, worker_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def _wait_for_terminal_status(workflow_id: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with get_session() as session:
            workflow = repository.get_workflow(session, workflow_id)
        if workflow is not None and workflow.status in (
            WorkflowStatus.COMPLETED, WorkflowStatus.FAILED,
        ):
            return workflow
        time.sleep(0.2)
    raise TimeoutError(f"workflow {workflow_id} never reached a terminal status within {timeout}s")


def test_a_workflow_completes_through_three_separate_processes(distributed_cluster):
    Base.metadata.create_all(engine)
    workflow_id = f"wf_triad_{int(time.time() * 1000)}"

    with get_session() as session:
        repository.create_workflow(session, workflow_id, "three-process test")
        repository.save_tasks(
            session,
            [
                Task(id=f"{workflow_id}_t1", workflow_id=workflow_id, description="t1",
                     input={"tool": "echo", "args": {"text": "hi"}}),
                Task(id=f"{workflow_id}_t2", workflow_id=workflow_id, description="t2",
                     dependencies=[f"{workflow_id}_t1"],
                     input={"tool": "uppercase", "args": {"text": "hi"}}),
            ],
        )

    # Exactly what `POST /workflows` does under `SCHEDULING_MODE=distributed`
    # (runtime/api/routes.py) -- hand the workflow id to whichever scheduler
    # process picks it up next, entirely over Redis.
    PendingWorkflowQueue(get_redis()).push(workflow_id)

    workflow = _wait_for_terminal_status(workflow_id)

    assert workflow.status == WorkflowStatus.COMPLETED
    with get_session() as session:
        tasks = {t.id: t for t in repository.get_tasks(session, workflow_id)}
    assert tasks[f"{workflow_id}_t1"].output == {"result": "hi"}
    assert tasks[f"{workflow_id}_t2"].output == {"result": "HI"}
