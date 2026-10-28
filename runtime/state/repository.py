from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import Workflow, WorkflowStatus, WorkflowWithTasks
from runtime.state.orm import (
    ExternalChargeRow,
    IdempotencyLedgerRow,
    OutboxEventRow,
    ReplanEventRow,
    RoutingCandidateRow,
    RoutingDecisionRow,
    RoutingOutcomeRow,
    TaskDependencyRow,
    TaskRow,
    ToolCallRow,
    WorkflowRow,
    ensure_aware,
    utcnow,
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
        error_category=row.error_category,
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        retry_after=ensure_aware(row.retry_after),
        replan_count=row.replan_count,
        max_replans=row.max_replans,
        created_at=ensure_aware(row.created_at),
        started_at=ensure_aware(row.started_at),
        completed_at=ensure_aware(row.completed_at),
        lease_owner=row.lease_owner,
        lease_expires_at=ensure_aware(row.lease_expires_at),
        fencing_token=row.fencing_token,
    )


def _workflow_from_row(row: WorkflowRow) -> Workflow:
    return Workflow(
        id=row.id,
        objective=row.objective,
        status=WorkflowStatus(row.status),
        plan_version=row.plan_version,
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
                max_retries=task.max_retries,
                max_replans=task.max_replans,
            )
        )
    session.flush()
    for task in tasks:
        for dep_id in task.dependencies:
            session.add(TaskDependencyRow(task_id=task.id, depends_on_task_id=dep_id))
    session.flush()


def add_task_dependency(session: Session, task_id: str, depends_on_task_id: str) -> None:
    """Adds one dependency edge onto an *existing* task -- what the
    replanner uses to splice a newly inserted prerequisite in front of the
    task that was blocked on it (Section 5.10), as opposed to `save_tasks`,
    which is for a brand-new task's initial dependency set.
    """
    session.add(TaskDependencyRow(task_id=task_id, depends_on_task_id=depends_on_task_id))
    session.flush()


def update_workflow_status(
    session: Session, workflow_id: str, status: WorkflowStatus, completed: bool = False
) -> None:
    row = session.get(WorkflowRow, workflow_id)
    if row is None:
        raise ValueError(f"unknown workflow_id {workflow_id}")
    row.status = status.value
    if completed:
        row.completed_at = utcnow()


def update_task(session: Session, task: Task) -> None:
    row = session.get(TaskRow, task.id)
    if row is None:
        raise ValueError(f"unknown task_id {task.id}")
    row.status = task.status.value
    row.input = task.input
    row.output = task.output
    row.error = task.error
    row.error_category = task.error_category
    row.retry_count = task.retry_count
    row.max_retries = task.max_retries
    row.retry_after = task.retry_after
    row.replan_count = task.replan_count
    row.max_replans = task.max_replans
    row.started_at = task.started_at
    row.completed_at = task.completed_at
    row.lease_owner = task.lease_owner
    row.lease_expires_at = task.lease_expires_at
    row.fencing_token = task.fencing_token


def commit_task_if_fencing_valid(session: Session, task: Task) -> bool:
    """Section 15F.4: "A worker may commit task completion only when its
    token and lease ownership still match the current record. Expired
    workers must not overwrite newer attempt results." Compares `task`'s
    fencing token/lease owner against the current row before applying the
    update; returns whether it was applied. A task that was never leased
    this round (its fencing fields are whatever was already on the row)
    trivially passes, so this is safe to call for every task uniformly, not
    just ones that went through `leases.acquire_lease`.
    """
    row = session.get(TaskRow, task.id)
    if row is None:
        raise ValueError(f"unknown task_id {task.id}")
    if row.fencing_token != task.fencing_token or row.lease_owner != task.lease_owner:
        return False
    update_task(session, task)
    return True


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


# -- Reliability (Section 12/13/15F): idempotency ledger, outbox events --


def get_idempotency_entry(session: Session, idempotency_key: str) -> IdempotencyLedgerRow | None:
    return session.get(IdempotencyLedgerRow, idempotency_key)


def upsert_idempotency_entry(
    session: Session,
    idempotency_key: str,
    workflow_id: str,
    task_id: str,
    status: str,
    result: dict | None = None,
) -> None:
    row = session.get(IdempotencyLedgerRow, idempotency_key)
    if row is None:
        session.add(
            IdempotencyLedgerRow(
                idempotency_key=idempotency_key,
                workflow_id=workflow_id,
                task_id=task_id,
                status=status,
                result=result,
            )
        )
    else:
        row.status = status
        row.result = result
        row.updated_at = utcnow()
    session.flush()


def record_outbox_event(session: Session, aggregate_id: str, event_type: str, payload: dict) -> None:
    session.add(OutboxEventRow(aggregate_id=aggregate_id, event_type=event_type, payload=payload))
    session.flush()


def get_undelivered_outbox_events(session: Session, limit: int = 100) -> list[OutboxEventRow]:
    stmt = (
        select(OutboxEventRow)
        .where(OutboxEventRow.delivered_at.is_(None))
        .order_by(OutboxEventRow.id)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def mark_outbox_event_delivered(session: Session, event_id: int) -> None:
    row = session.get(OutboxEventRow, event_id)
    if row is not None:
        row.delivered_at = utcnow()
        session.flush()


# -- Simulates a third-party system's own idempotency support, for the
# `idempotent_charge` demo tool (runtime/tools/registry.py) --


def get_external_charge(session: Session, idempotency_key: str) -> ExternalChargeRow | None:
    return session.get(ExternalChargeRow, idempotency_key)


def record_external_charge(session: Session, idempotency_key: str, amount: float) -> None:
    session.add(ExternalChargeRow(idempotency_key=idempotency_key, amount=amount))
    session.flush()


def count_external_charges(session: Session) -> int:
    return len(session.scalars(select(ExternalChargeRow)).all())


# -- Dynamic replanning (Section 5.10/15J Phase 5) --


def bump_plan_version(session: Session, workflow_id: str) -> int:
    row = session.get(WorkflowRow, workflow_id)
    if row is None:
        raise ValueError(f"unknown workflow_id {workflow_id}")
    row.plan_version += 1
    session.flush()
    return row.plan_version


def record_replan_event(
    session: Session,
    workflow_id: str,
    blocked_task_id: str,
    context: str,
    new_task_ids: list[str],
    plan_version: int,
) -> None:
    session.add(
        ReplanEventRow(
            workflow_id=workflow_id,
            blocked_task_id=blocked_task_id,
            context=context,
            new_task_ids=new_task_ids,
            plan_version=plan_version,
        )
    )
    session.flush()


def get_replan_events(session: Session, workflow_id: str) -> list[dict]:
    stmt = (
        select(ReplanEventRow)
        .where(ReplanEventRow.workflow_id == workflow_id)
        .order_by(ReplanEventRow.id)
    )
    return [
        {
            "blocked_task_id": row.blocked_task_id,
            "context": row.context,
            "new_task_ids": row.new_task_ids,
            "plan_version": row.plan_version,
            "created_at": row.created_at,
        }
        for row in session.scalars(stmt).all()
    ]
