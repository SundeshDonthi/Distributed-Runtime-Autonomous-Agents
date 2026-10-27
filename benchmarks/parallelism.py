"""Reproduces Section 6's worked example: three independent tasks taking 5s,
6s, and 4s sequentially should take roughly max(durations), not sum(durations),
when the scheduler dispatches them concurrently.

Run:
    python -m benchmarks.parallelism
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from runtime.models.task import Task
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.orm import Base

DURATIONS = {"task_a": 5, "task_b": 6, "task_c": 4}


def _build_tasks(workflow_id: str) -> list[Task]:
    return [
        Task(
            id=f"{workflow_id}_{name}",
            workflow_id=workflow_id,
            description=f"{name} ({seconds}s)",
            dependencies=[],
            input={"tool": "delay", "args": {"seconds": seconds, "text": name}},
        )
        for name, seconds in DURATIONS.items()
    ]


def main() -> None:
    engine = create_engine("sqlite:///./benchmark.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()

    workflow_id = f"bench_{uuid.uuid4().hex[:8]}"
    repository.create_workflow(session, workflow_id, "parallelism benchmark")
    tasks = _build_tasks(workflow_id)
    repository.save_tasks(session, tasks)
    session.commit()

    sequential_estimate = sum(DURATIONS.values())
    start = time.perf_counter()
    run_workflow(session, workflow_id, max_concurrency=len(tasks))
    elapsed = time.perf_counter() - start
    session.close()

    print(f"Sequential estimate: {sequential_estimate:.1f}s")
    print(f"Actual (parallel):   {elapsed:.1f}s")
    print(f"Parallel speedup:    {sequential_estimate / elapsed:.2f}x")


if __name__ == "__main__":
    main()
