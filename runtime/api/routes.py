from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from runtime.models.task import Task
from runtime.models.workflow import Workflow, WorkflowWithTasks
from runtime.planner.planner import RuleBasedPlanner
from runtime.planner.validator import PlanValidationError, validate_plan
from runtime.scheduler.queue import PendingWorkflowQueue
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.db import get_session
from runtime.state.redis_client import get_redis
from runtime.tools.registry import list_tools, get_tool_metadata

router = APIRouter()
planner = RuleBasedPlanner()

# Section 15J Phase 8: "embedded" (the default) keeps Phases 1-7's behavior --
# this process runs the workflow itself, inline, via FastAPI's own
# `BackgroundTasks`. "distributed" instead hands the workflow id to a
# standalone `runtime.scheduler.scheduler_process`, which dispatches its
# tasks to independent worker processes -- what Docker Compose's `api`
# service sets so the API, scheduler, and workers are genuinely separate OS
# processes. Read once at import time, like `DATABASE_URL`/`REDIS_URL`
# elsewhere in this project, not re-read per request.
SCHEDULING_MODE = os.environ.get("SCHEDULING_MODE", "embedded")


class CreateWorkflowRequest(BaseModel):
    objective: str


def _run_workflow_background(workflow_id: str) -> None:
    with get_session() as session:
        run_workflow(session, workflow_id)


@router.post("/workflows", response_model=Workflow, status_code=201)
def create_workflow(req: CreateWorkflowRequest, background_tasks: BackgroundTasks) -> Workflow:
    workflow_id = f"wf_{uuid.uuid4().hex[:8]}"

    tasks: list[Task] = planner.generate_plan(workflow_id, req.objective)
    try:
        validate_plan(tasks)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with get_session() as session:
        workflow = repository.create_workflow(session, workflow_id, req.objective)
        repository.save_tasks(session, tasks)

    if SCHEDULING_MODE == "distributed":
        PendingWorkflowQueue(get_redis()).push(workflow_id)
    else:
        background_tasks.add_task(_run_workflow_background, workflow_id)
    return workflow


@router.get("/workflows/{workflow_id}", response_model=WorkflowWithTasks)
def get_workflow(workflow_id: str) -> WorkflowWithTasks:
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@router.get("/workflows/{workflow_id}/tasks", response_model=list[Task])
def get_workflow_tasks(workflow_id: str) -> list[Task]:
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return repository.get_tasks(session, workflow_id)


@router.get("/tools")
def get_tools() -> list[dict]:
    """Section 15H.2's `GET /tools`: every currently registered tool,
    in-process or MCP-discovered, with the metadata the router scores it on.
    """
    return [
        {
            "name": meta.name,
            "capability": meta.capability,
            "base_latency_seconds": meta.base_latency_seconds,
            "reliability": meta.reliability,
            "cost_per_call": meta.cost_per_call,
            "input_schema": meta.input_schema,
        }
        for meta in (get_tool_metadata(name) for name in list_tools())
    ]


@router.get("/workflows/{workflow_id}/routing-decisions")
def get_workflow_routing_decisions(workflow_id: str) -> list[dict]:
    """Section 15H.2's `GET /workflows/{id}/routing-decisions`: every routed
    decision for this workflow, with its full candidate set and outcome
    (None if the task hasn't finished running yet).
    """
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return repository.get_routing_decisions_for_workflow(session, workflow_id)


@router.get("/workflows/{workflow_id}/replan-events")
def get_workflow_replan_events(workflow_id: str) -> list[dict]:
    """Every graph mutation (Section 5.10/15J Phase 5) this workflow has gone
    through: which task was blocked, why, what got inserted, and which plan
    version it produced.
    """
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return repository.get_replan_events(session, workflow_id)


@router.get("/router-models")
def get_router_models() -> list[dict]:
    """Every trained router model (Section 15G.2), in whatever rollout stage
    it's in -- shadow, active, or retired (Section 15D.3).
    """
    with get_session() as session:
        return repository.list_router_models(session)


@router.post("/admin/router/models/{model_version}/activate")
def activate_router_model(model_version: str) -> dict:
    """Section 15H.2's own named endpoint: promotes a model to "active"
    (Section 15D.3's rollout stage 3/4) and retires whatever was active
    before it, keeping the single-active invariant on a real promotion even
    though the DB schema itself doesn't enforce it. Does not, by itself,
    change which `routing_mode` any given workflow run actually uses --
    that's still passed explicitly to `run_workflow` by whoever starts it.
    """
    with get_session() as session:
        target = repository.get_router_model(session, model_version)
        if target is None:
            raise HTTPException(status_code=404, detail="model_version not found")

        previous_active = repository.get_active_router_model(session)
        if previous_active is not None and previous_active["model_version"] != model_version:
            repository.set_router_model_status(session, previous_active["model_version"], "retired")
        repository.set_router_model_status(session, model_version, "active")
        return repository.get_router_model(session, model_version)


@router.get("/workflows/{workflow_id}/graph")
def get_workflow_graph(workflow_id: str) -> dict:
    """Section 15J Phase 8's observability goal, the structural half: every
    task as a node (with its status and whatever tool/capability it addresses)
    and a dependency edge for every `task_dependencies` row -- enough to
    render a plan, or what a replan changed about it, as an actual DAG rather
    than a flat task list. Reading this doesn't depend on which OS process
    (API, scheduler, or any worker) last touched the workflow -- Postgres is
    still the one shared source of truth Phase 8's split into separate
    processes doesn't change.
    """
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")

    nodes = [
        {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "tool": (task.input or {}).get("tool"),
            "capability": (task.input or {}).get("capability"),
        }
        for task in workflow.tasks
    ]
    edges = [
        {"from": depends_on_id, "to": task.id}
        for task in workflow.tasks
        for depends_on_id in task.dependencies
    ]
    return {"workflow_id": workflow_id, "plan_version": workflow.plan_version, "nodes": nodes, "edges": edges}


@router.get("/workflows/{workflow_id}/trace")
def get_workflow_trace(workflow_id: str) -> list[dict]:
    """Section 15J Phase 8's observability goal, the temporal half: one
    merged, time-ordered timeline of everything that happened to a workflow
    -- every task state transition (the outbox, Section 15F.4), every routing
    decision, and every replan -- regardless of which OS process actually did
    it. Before Phase 8 this was always reconstructable by tailing one
    process's stdout; once execution is genuinely spread across an API
    process, a scheduler process, and any number of worker processes, there
    is no single process left to tail, so this is now the only coherent way
    to see what happened to a given workflow.
    """
    with get_session() as session:
        workflow = repository.get_workflow(session, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        outbox_events = repository.get_outbox_events_for_workflow(session, workflow_id)
        routing_decisions = repository.get_routing_decisions_for_workflow(session, workflow_id)
        replan_events = repository.get_replan_events(session, workflow_id)

    events = [
        {"timestamp": e["created_at"], "type": e["event_type"], "source": "outbox", "detail": e}
        for e in outbox_events
    ]
    events += [
        {"timestamp": d["created_at"], "type": "routing.decision", "source": "routing", "detail": d}
        for d in routing_decisions
    ]
    events += [
        {"timestamp": r["created_at"], "type": "replan", "source": "replanner", "detail": r}
        for r in replan_events
    ]
    events.sort(key=lambda e: e["timestamp"])
    return events
