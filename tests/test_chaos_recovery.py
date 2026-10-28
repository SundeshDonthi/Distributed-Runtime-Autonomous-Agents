from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.db import DATABASE_URL, engine, get_session
from runtime.state.orm import Base

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
    not _postgres_reachable(),
    reason="chaos recovery needs a live Postgres DATABASE_URL -- separate OS "
    "processes can't share an in-memory SQLite DB",
)


def test_killing_the_process_mid_workflow_does_not_lose_completed_work():
    """Phase 4's own definition of done: "killing workers does not erase
    completed tasks and duplicate effects are controlled or reconciled."

    `fast` -> `slow` are in separate rounds on purpose (see scheduler.py's
    docstring: commits happen per round, not per task within a round, since
    concurrent tasks in one round only finish together via one
    `asyncio.gather`) -- this is what makes `fast`'s completion durable
    *before* the process gets killed partway through `slow`.
    """
    Base.metadata.create_all(engine)
    workflow_id = f"wf_chaos_{int(time.time() * 1000)}"
    fast_id, slow_id = f"{workflow_id}_fast", f"{workflow_id}_slow"

    with get_session() as session:
        repository.create_workflow(session, workflow_id, "chaos test")
        repository.save_tasks(
            session,
            [
                Task(id=fast_id, workflow_id=workflow_id, description="fast",
                     input={"tool": "echo", "args": {"text": "fast-done"}}),
                Task(id=slow_id, workflow_id=workflow_id, description="slow",
                     dependencies=[fast_id],
                     input={"tool": "delay", "args": {"seconds": 3}}),
            ],
        )

    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.chaos_run_workflow", workflow_id, "1.0"],
        cwd=REPO_ROOT,
    )
    try:
        time.sleep(1.5)  # fast's round should be done & committed; slow mid-flight
        proc.kill()
        proc.wait(timeout=10)

        with get_session() as session:
            after_kill = {t.id: t for t in repository.get_tasks(session, workflow_id)}
        assert after_kill[fast_id].status == TaskStatus.COMPLETED
        assert after_kill[slow_id].status == TaskStatus.RUNNING  # orphaned, lease not yet reclaimed
        fast_output_before = after_kill[fast_id].output

        time.sleep(1.5)  # let the 1.0s lease actually elapse before recovering

        with get_session() as session:
            status = run_workflow(session, workflow_id, lease_seconds=1.0)

        assert status == WorkflowStatus.COMPLETED
        with get_session() as session:
            final = {t.id: t for t in repository.get_tasks(session, workflow_id)}

        assert final[fast_id].status == TaskStatus.COMPLETED
        assert final[fast_id].output == fast_output_before
        assert final[fast_id].retry_count == 0  # never touched again

        assert final[slow_id].status == TaskStatus.COMPLETED
        assert final[slow_id].retry_count >= 1  # proves it WAS reclaimed, not just lucky timing
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
