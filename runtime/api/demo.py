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
from runtime.models.task import Task
from runtime.investigations.models import InvestigationSpec
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
    workflow_id = f"demo_{uuid.uuid4().hex}"
    tasks = build_plan(workflow_id, request.source, request.scenario == "recover")
    return submit_workflow(workflow_id, tasks, f"Compare Python HTTP clients · {request.source} · {request.scenario}", background_tasks)


def submit_workflow(workflow_id: str, tasks: list[Task], objective: str, background_tasks: BackgroundTasks, parent_tasks: list[Task] | None = None) -> Workflow:
    """Shared admission, persistence, and dispatch for bounded public workloads."""
    from runtime.api.routes import SCHEDULING_MODE, _run_workflow_background
    from runtime.scheduler.queue import PendingWorkflowQueue
    from runtime.state.redis_client import get_redis
    from runtime.investigations.plans import reuse_checkpoints

    validate_plan(tasks)
    with _lock:
        now = time.monotonic()
        while _submissions and _submissions[0] < now - 60:
            _submissions.popleft()
        with get_session() as session:
            active = session.scalar(select(func.count()).select_from(WorkflowRow).where(
                WorkflowRow.status.in_(["PLANNING", "RUNNING"])))
            if len(_submissions) >= 12 or active >= 4:
                raise HTTPException(429, "Demo is busy. Please retry shortly.", headers={"Retry-After": "30"})
            workflow = repository.create_workflow(session, workflow_id, objective)
            repository.save_tasks(session, tasks)
            if parent_tasks is not None:
                reuse_checkpoints(session, tasks, parent_tasks)
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


@router.post("/investigations", response_model=Workflow, status_code=201)
def create_investigation(spec: InvestigationSpec, background_tasks: BackgroundTasks):
    from runtime.investigations.plans import build_plan as build_investigation
    workflow_id = f"investigation_{uuid.uuid4().hex}"
    return submit_workflow(workflow_id, build_investigation(workflow_id, spec), "Evaluate Python HTTP clients against explicit requirements", background_tasks)


def load_investigation(workflow_id):
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
    if workflow is None:
        raise HTTPException(404, "Investigation not found")
    brief = next((t for t in workflow.tasks if (t.input or {}).get("tool") == "investigation.assemble"), None)
    if brief is None:
        raise HTTPException(404, "Workflow is not an investigation")
    return workflow, brief


@router.get("/investigations/{workflow_id}")
def get_investigation(workflow_id: str):
    workflow, brief = load_investigation(workflow_id)
    return {"workflow": workflow, "spec": brief.input["spec"],
            "parent_workflow_id": brief.input.get("parent_workflow_id"),
            "reused_checkpoints": sum(bool((task.input or {}).get("reused_from")) for task in workflow.tasks),
            "report": brief.output}


@router.post("/investigations/{workflow_id}/revisions", response_model=Workflow, status_code=201)
def revise_investigation(workflow_id: str, spec: InvestigationSpec, background_tasks: BackgroundTasks):
    from runtime.investigations.plans import build_plan as build_investigation
    parent, _brief = load_investigation(workflow_id)
    if parent.status.value not in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "Wait for the parent investigation to finish")
    revision_id = f"investigation_{uuid.uuid4().hex}"
    tasks = build_investigation(revision_id, spec, parent_id=workflow_id)
    return submit_workflow(revision_id, tasks, "Revise technical investigation", background_tasks, parent.tasks)
