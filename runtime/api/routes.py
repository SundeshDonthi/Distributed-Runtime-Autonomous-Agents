from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from runtime.models.task import Task
from runtime.models.workflow import Workflow, WorkflowWithTasks
from runtime.planner.planner import RuleBasedPlanner
from runtime.planner.validator import PlanValidationError, validate_plan
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.db import get_session
from runtime.tools.registry import list_tools, get_tool_metadata

router = APIRouter()
planner = RuleBasedPlanner()


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
