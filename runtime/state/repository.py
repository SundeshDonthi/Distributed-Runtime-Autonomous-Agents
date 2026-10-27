from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import Workflow, WorkflowStatus, WorkflowWithTasks
from runtime.state.orm import TaskDependencyRow, TaskRow, WorkflowRow


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
