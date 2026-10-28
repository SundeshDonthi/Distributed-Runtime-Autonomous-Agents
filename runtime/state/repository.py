from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import Workflow, WorkflowStatus, WorkflowWithTasks
from runtime.state.orm import (
    RoutingCandidateRow,
    RoutingDecisionRow,
    RoutingOutcomeRow,
    TaskDependencyRow,
    TaskRow,
    ToolCallRow,
    WorkflowRow,
)


def _task_from_row(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        workflow_id=row.workflow_id,
        description=row.description,
        dependencies=[d.depends_on_task_id for d in row.dependencies],
        required_capabilities=row.required_capabilities or [],
        status=TaskStatus(row.status),
        input=row.input,
        output=row.output,
        error=row.error,
        retry_count=row.retry_count,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _workflow_from_row(row: WorkflowRow) -> Workflow:
    return Workflow(
        id=row.id,
        objective=row.objective,
        status=WorkflowStatus(row.status),
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def create_workflow(session: Session, workflow_id: str, objective: str) -> Workflow:
    row = WorkflowRow(id=workflow_id, objective=objective, status=WorkflowStatus.PLANNING.value)
    session.add(row)
    session.flush()
    return _workflow_from_row(row)


def save_tasks(session: Session, tasks: list[Task]) -> None:
    for task in tasks:
        session.add(
            TaskRow(
                id=task.id,
                workflow_id=task.workflow_id,
                description=task.description,
                required_capabilities=task.required_capabilities,
                status=task.status.value,
                input=task.input,
            )
        )
    session.flush()
    for task in tasks:
        for dep_id in task.dependencies:
            session.add(TaskDependencyRow(task_id=task.id, depends_on_task_id=dep_id))
    session.flush()


def update_workflow_status(
    session: Session, workflow_id: str, status: WorkflowStatus, completed: bool = False
) -> None:
    row = session.get(WorkflowRow, workflow_id)
    if row is None:
        raise ValueError(f"unknown workflow_id {workflow_id}")
    row.status = status.value
    if completed:
        from runtime.state.orm import utcnow

        row.completed_at = utcnow()


def update_task(session: Session, task: Task) -> None:
    row = session.get(TaskRow, task.id)
    if row is None:
        raise ValueError(f"unknown task_id {task.id}")
    row.status = task.status.value
    row.input = task.input
    row.output = task.output
    row.error = task.error
    row.retry_count = task.retry_count
    row.started_at = task.started_at
    row.completed_at = task.completed_at


def get_workflow(session: Session, workflow_id: str) -> WorkflowWithTasks | None:
    row = session.get(
        WorkflowRow,
        workflow_id,
        options=[selectinload(WorkflowRow.tasks).selectinload(TaskRow.dependencies)],
    )
    if row is None:
        return None
    workflow = _workflow_from_row(row)
    tasks = [_task_from_row(t) for t in row.tasks]
    return WorkflowWithTasks(**workflow.model_dump(), tasks=tasks)


def get_tasks(session: Session, workflow_id: str) -> list[Task]:
    stmt = (
        select(TaskRow)
        .where(TaskRow.workflow_id == workflow_id)
        .options(selectinload(TaskRow.dependencies))
    )
    rows = session.scalars(stmt).all()
    return [_task_from_row(r) for r in rows]


# -- Routing (Section 15D/15G): decisions, candidates, outcomes, tool calls --


def record_tool_call(
    session: Session,
    task_id: str,
    tool_name: str,
    input: dict | None,
    output: dict | None,
    latency_ms: float,
    status: str,
    error: str | None,
) -> None:
    session.add(
        ToolCallRow(
            task_id=task_id,
            tool_name=tool_name,
            input=input,
            output=output,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
    )
    session.flush()


def create_routing_decision(
    session: Session,
    task_id: str,
    workflow_id: str,
    capability: str,
    ranked_candidates: list[tuple[str, float]],
    selected_tool: str,
    weights: dict,
    was_exploration: bool = False,
    model_version: str = "heuristic-v1",
) -> str:
    """Persists a routing decision and the full ranked candidate set (not
    just the winner -- Section 15G.2). `selected_tool` is passed explicitly
    rather than assumed to be `ranked_candidates[0]`, since an exploratory
    decision picks something other than the top-ranked candidate on purpose.
    Returns the decision id so the caller can attach the eventual outcome
    once the chosen tool has run.
    """
    decision = RoutingDecisionRow(
        task_id=task_id,
        workflow_id=workflow_id,
        capability=capability,
        model_version=model_version,
        weights=weights,
        selected_tool=selected_tool,
        was_exploration=was_exploration,
    )
    session.add(decision)
    session.flush()

    for rank, (tool_name, score) in enumerate(ranked_candidates):
        session.add(
            RoutingCandidateRow(
                decision_id=decision.id, tool_name=tool_name, score=score, rank=rank
            )
        )
    session.flush()
    return decision.id


def create_routing_outcome(
    session: Session, decision_id: str, success: bool, latency_ms: float, error: str | None
) -> None:
    session.add(
        RoutingOutcomeRow(
            decision_id=decision_id, success=success, latency_ms=latency_ms, error=error
        )
    )
    session.flush()


def get_recent_tool_calls(session: Session, tool_name: str, limit: int) -> list[ToolCallRow]:
    """Most recent calls first -- the window a router's observed-stats
    computation (Section 15B.3) draws from. Ordered by `id` (insertion order)
    rather than `created_at`, which two fast, consecutive calls could tie on.
    Excludes nothing in-flight: by construction this is only ever called
    before the *current* attempt is recorded, so it can't leak the current
    outcome into its own feature.
    """
    stmt = (
        select(ToolCallRow)
        .where(ToolCallRow.tool_name == tool_name)
        .order_by(desc(ToolCallRow.id))
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def _routing_decision_dict(row: RoutingDecisionRow) -> dict:
    return {
        "decision_id": row.id,
        "task_id": row.task_id,
        "workflow_id": row.workflow_id,
        "capability": row.capability,
        "model_version": row.model_version,
        "weights": row.weights,
        "selected_tool": row.selected_tool,
        "was_exploration": row.was_exploration,
        "candidates": [
            {"tool_name": c.tool_name, "score": c.score, "rank": c.rank}
            for c in sorted(row.candidates, key=lambda c: c.rank)
        ],
        "created_at": row.created_at,
    }


def get_routing_decision(session: Session, decision_id: str) -> dict | None:
    """A single decision and its candidate set, regardless of whether an
    outcome has been recorded yet -- what `GET /workflows/{id}/routing-
    decisions` (Section 15H.2) would serve, and useful for inspecting a
    decision immediately after it's made, before the task has run.
    """
    row = session.get(
        RoutingDecisionRow,
        decision_id,
        options=[selectinload(RoutingDecisionRow.candidates), selectinload(RoutingDecisionRow.outcome)],
    )
    if row is None:
        return None
    result = _routing_decision_dict(row)
    result["outcome"] = (
        None
        if row.outcome is None
        else {
            "success": row.outcome.success,
            "latency_ms": row.outcome.latency_ms,
            "error": row.outcome.error,
        }
    )
    return result


def get_routing_decisions_for_workflow(session: Session, workflow_id: str) -> list[dict]:
    """Every routing decision for a workflow, including ones whose task
    hasn't finished yet (`outcome` is None then) -- what `GET /workflows/{id}
    /routing-decisions` (Section 15H.2) serves. Unlike `get_routing_dataset`,
    this is for inspecting a workflow's routing activity, not for building a
    labeled training set, so an unresolved decision is still worth showing.
    """
    stmt = (
        select(RoutingDecisionRow)
        .where(RoutingDecisionRow.workflow_id == workflow_id)
        .options(
            selectinload(RoutingDecisionRow.candidates),
            selectinload(RoutingDecisionRow.outcome),
        )
    )
    results = []
    for row in session.scalars(stmt).all():
        result = _routing_decision_dict(row)
        result["outcome"] = (
            None
            if row.outcome is None
            else {
                "success": row.outcome.success,
                "latency_ms": row.outcome.latency_ms,
                "error": row.outcome.error,
            }
        )
        results.append(result)
    return results


def get_routing_dataset(session: Session, workflow_id: str | None = None) -> list[dict]:
    """One row per routing decision that has a recorded outcome, flattened
    for training/evaluation (Section 15D's dataset, Section 15I's
    evaluation). Each row names its own candidate set and weights so a
    different capability's tools never get mixed into the same example by
    accident, and only decisions with a resolved outcome are included so a
    label always exists.
    """
    stmt = (
        select(RoutingDecisionRow)
        .join(RoutingOutcomeRow, RoutingDecisionRow.id == RoutingOutcomeRow.decision_id)
        .options(
            selectinload(RoutingDecisionRow.candidates),
            selectinload(RoutingDecisionRow.outcome),
        )
    )
    if workflow_id is not None:
        stmt = stmt.where(RoutingDecisionRow.workflow_id == workflow_id)

    rows = session.scalars(stmt).all()
    results = []
    for row in rows:
        result = _routing_decision_dict(row)
        result["success"] = row.outcome.success
        result["latency_ms"] = row.outcome.latency_ms
        result["error"] = row.outcome.error
        results.append(result)
    return results
