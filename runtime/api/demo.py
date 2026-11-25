"""Public demo: bounded workloads, no arbitrary tools or remote URLs."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from runtime.models.workflow import Workflow
from runtime.planner.validator import validate_plan
from runtime.state import repository
from runtime.state.db import get_session
from runtime.state.orm import WorkflowRow
from runtime.workloads.repositories import build_plan

router = APIRouter(prefix="/demo")
_lock = threading.Lock()
_submissions: deque[float] = deque()


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["snapshot", "live"] = "snapshot"
    scenario: Literal["compare", "recover"] = "compare"


@router.post("/runs", response_model=Workflow, status_code=201)
def create_run(request: DemoRequest, background_tasks: BackgroundTasks) -> Workflow:
    from runtime.api.routes import SCHEDULING_MODE, _run_workflow_background
    from runtime.scheduler.queue import PendingWorkflowQueue
    from runtime.state.redis_client import get_redis

    workflow_id = f"demo_{uuid.uuid4().hex}"
    tasks = build_plan(workflow_id, request.source, request.scenario == "recover")
    validate_plan(tasks)
    # A global process budget supplements DB admission. Deployment uses one API
    # instance; cross-replica rate limiting requires a shared limiter.
    with _lock:
        now = time.monotonic()
        while _submissions and _submissions[0] < now - 60:
            _submissions.popleft()
        with get_session() as session:
            active = session.scalar(select(func.count()).select_from(WorkflowRow).where(
                WorkflowRow.status.in_(["PLANNING", "RUNNING"])))
            if len(_submissions) >= 12 or active >= 4:
                raise HTTPException(429, "Demo is busy. Please retry shortly.", headers={"Retry-After": "30"})
            workflow = repository.create_workflow(session, workflow_id, f"Compare Python HTTP clients · {request.source} · {request.scenario}")
            repository.save_tasks(session, tasks)
        _submissions.append(now)
    if SCHEDULING_MODE == "distributed":
        PendingWorkflowQueue(get_redis()).push(workflow_id)
    else:
        background_tasks.add_task(_run_workflow_background, workflow_id)
    return workflow


@router.get("/benchmark")
def benchmark_evidence() -> dict:
    """Checked-in observations from the controlled experiment, never per-run estimates."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "benchmarks" / "results" / "controlled-demo.json"
    if not path.exists():
        raise HTTPException(404, "No verified benchmark artifact is packaged")
    return json.loads(path.read_text())
