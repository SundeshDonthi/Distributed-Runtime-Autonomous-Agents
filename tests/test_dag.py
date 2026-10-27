from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.scheduler.dag import has_failed_dependency, is_workflow_done, ready_tasks, workflow_succeeded


def make_task(task_id: str, deps: list[str], status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(id=task_id, workflow_id="wf_1", description=task_id, dependencies=deps, status=status)


def test_leaf_tasks_are_ready_immediately():
    tasks = [make_task("a", []), make_task("b", ["a"])]
    assert [t.id for t in ready_tasks(tasks)] == ["a"]


def test_task_becomes_ready_once_dependency_completes():
    tasks = [
        make_task("a", [], status=TaskStatus.COMPLETED),
        make_task("b", ["a"]),
    ]
    assert [t.id for t in ready_tasks(tasks)] == ["b"]


def test_task_not_ready_until_all_dependencies_complete():
    tasks = [
        make_task("a", [], status=TaskStatus.COMPLETED),
        make_task("b", [], status=TaskStatus.RUNNING),
        make_task("c", ["a", "b"]),
    ]
    assert "c" not in {t.id for t in ready_tasks(tasks)}


def test_failed_dependency_detected():
    tasks = [make_task("a", [], status=TaskStatus.FAILED), make_task("b", ["a"])]
    by_id = {t.id: t for t in tasks}
    assert has_failed_dependency(by_id["b"], by_id) is True


def test_workflow_done_and_succeeded():
    tasks = [make_task("a", [], status=TaskStatus.COMPLETED)]
    assert is_workflow_done(tasks) is True
    assert workflow_succeeded(tasks) is True

    tasks = [make_task("a", [], status=TaskStatus.FAILED)]
    assert is_workflow_done(tasks) is True
    assert workflow_succeeded(tasks) is False
