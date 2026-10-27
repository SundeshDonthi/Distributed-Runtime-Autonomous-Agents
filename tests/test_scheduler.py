from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository


def _echo_task(task_id: str, workflow_id: str, deps: list[str]) -> Task:
    return Task(
        id=task_id,
        workflow_id=workflow_id,
        description=task_id,
        dependencies=deps,
        input={"tool": "echo", "args": {"text": task_id}},
    )


def test_diamond_dag_executes_in_dependency_order(db_session):
    workflow_id = "wf_diamond"
    repository.create_workflow(db_session, workflow_id, "diamond objective")
    tasks = [
        _echo_task("a", workflow_id, []),
        _echo_task("b", workflow_id, ["a"]),
        _echo_task("c", workflow_id, ["a"]),
        _echo_task("d", workflow_id, ["b", "c"]),
    ]
    repository.save_tasks(db_session, tasks)

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.COMPLETED
    final = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert all(t.status == TaskStatus.COMPLETED for t in final.values())
    assert final["a"].completed_at <= final["b"].started_at
    assert final["a"].completed_at <= final["c"].started_at
    assert max(final["b"].completed_at, final["c"].completed_at) <= final["d"].started_at


def test_failed_task_cancels_downstream(db_session):
    workflow_id = "wf_failure"
    repository.create_workflow(db_session, workflow_id, "failure objective")
    tasks = [
        Task(
            id="bad",
            workflow_id=workflow_id,
            description="bad",
            dependencies=[],
            input={"tool": "does_not_exist", "args": {}},
        ),
        _echo_task("downstream", workflow_id, ["bad"]),
    ]
    repository.save_tasks(db_session, tasks)

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.FAILED
    final = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert final["bad"].status == TaskStatus.FAILED
    assert final["downstream"].status == TaskStatus.CANCELLED
