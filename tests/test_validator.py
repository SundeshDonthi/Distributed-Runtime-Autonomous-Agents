from __future__ import annotations

import pytest

from runtime.models.task import Task
from runtime.planner.validator import PlanValidationError, validate_plan


def make_task(task_id: str, deps: list[str]) -> Task:
    return Task(id=task_id, workflow_id="wf_1", description=task_id, dependencies=deps)


def test_valid_dag_passes():
    tasks = [
        make_task("a", []),
        make_task("b", []),
        make_task("c", ["a", "b"]),
    ]
    validate_plan(tasks)  # should not raise


def test_missing_dependency_rejected():
    tasks = [make_task("a", ["ghost"])]
    with pytest.raises(PlanValidationError, match="unknown task"):
        validate_plan(tasks)


def test_cycle_rejected():
    tasks = [
        make_task("a", ["c"]),
        make_task("b", ["a"]),
        make_task("c", ["b"]),
    ]
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(tasks)


def test_duplicate_ids_rejected():
    tasks = [make_task("a", []), make_task("a", [])]
    with pytest.raises(PlanValidationError, match="duplicate"):
        validate_plan(tasks)
