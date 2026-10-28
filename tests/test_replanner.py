from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.planner.planner import RuleBasedPlanner
from runtime.recovery.replanner import replan_for_missing_prerequisite
from runtime.state import repository


def _blocked_task(workflow_id: str, task_id: str = "b", **overrides) -> Task:
    defaults = dict(
        id=task_id,
        workflow_id=workflow_id,
        description="blocked",
        status=TaskStatus.FAILED,
        error="need supporting data for 'x'",
        error_category="needs_replan",
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_successful_replan_adds_a_prerequisite_and_unblocks_the_task(db_session):
    workflow_id = "wf1"
    repository.create_workflow(db_session, workflow_id, "replan test")
    blocked = _blocked_task(workflow_id)
    repository.save_tasks(db_session, [blocked])
    tasks = [blocked]

    new_tasks = replan_for_missing_prerequisite(db_session, tasks, blocked, RuleBasedPlanner())

    assert len(new_tasks) == 1
    assert new_tasks[0].id == "b_prereq_0"
    assert blocked.status == TaskStatus.PENDING
    assert blocked.dependencies == ["b_prereq_0"]
    assert blocked.replan_count == 1
    assert blocked.error is None
    assert blocked.error_category is None

    wf = repository.get_workflow(db_session, workflow_id)
    assert wf.plan_version == 2
    events = repository.get_replan_events(db_session, workflow_id)
    assert len(events) == 1
    assert events[0]["blocked_task_id"] == "b"
    assert events[0]["new_task_ids"] == ["b_prereq_0"]


def test_replan_budget_exhausted_fails_the_task(db_session):
    workflow_id = "wf2"
    repository.create_workflow(db_session, workflow_id, "budget test")
    blocked = _blocked_task(workflow_id, replan_count=2, max_replans=2)
    repository.save_tasks(db_session, [blocked])

    new_tasks = replan_for_missing_prerequisite(db_session, [blocked], blocked, RuleBasedPlanner())

    assert new_tasks == []
    assert blocked.status == TaskStatus.FAILED
    assert "replan budget exhausted" in blocked.error
    # No graph mutation should have happened.
    assert repository.get_replan_events(db_session, workflow_id) == []


def test_planner_producing_a_colliding_task_id_fails_cleanly(db_session):
    workflow_id = "wf3"
    repository.create_workflow(db_session, workflow_id, "collision test")
    blocked = _blocked_task(workflow_id)
    already_there = Task(id="b_prereq_0", workflow_id=workflow_id, description="already exists")
    repository.save_tasks(db_session, [blocked, already_there])

    new_tasks = replan_for_missing_prerequisite(
        db_session, [blocked, already_there], blocked, RuleBasedPlanner()
    )

    assert new_tasks == []
    assert blocked.status == TaskStatus.FAILED
    assert "colliding task id" in blocked.error


def test_subplan_task_with_its_own_dependency_is_rejected(db_session):
    class BadPlanner(RuleBasedPlanner):
        def generate_subplan(self, workflow_id, blocked_task_id, replan_index, context):
            return [
                Task(
                    id=f"{blocked_task_id}_bad",
                    workflow_id=workflow_id,
                    description="bad",
                    dependencies=["something_else"],
                )
            ]

    workflow_id = "wf4"
    repository.create_workflow(db_session, workflow_id, "bad subplan test")
    blocked = _blocked_task(workflow_id)
    repository.save_tasks(db_session, [blocked])

    new_tasks = replan_for_missing_prerequisite(db_session, [blocked], blocked, BadPlanner())

    assert new_tasks == []
    assert blocked.status == TaskStatus.FAILED
    assert "must have no dependencies" in blocked.error


def test_empty_subplan_fails_the_task(db_session):
    class EmptyPlanner(RuleBasedPlanner):
        def generate_subplan(self, workflow_id, blocked_task_id, replan_index, context):
            return []

    workflow_id = "wf5"
    repository.create_workflow(db_session, workflow_id, "empty subplan test")
    blocked = _blocked_task(workflow_id)
    repository.save_tasks(db_session, [blocked])

    new_tasks = replan_for_missing_prerequisite(db_session, [blocked], blocked, EmptyPlanner())

    assert new_tasks == []
    assert blocked.status == TaskStatus.FAILED
    assert "produced no tasks" in blocked.error


def test_completed_work_elsewhere_in_the_graph_is_untouched_by_a_replan(db_session):
    # Section 5.10: "The planner should only modify unfinished portions of
    # the graph so completed work remains reusable."
    workflow_id = "wf6"
    repository.create_workflow(db_session, workflow_id, "reuse test")
    completed = Task(
        id="a", workflow_id=workflow_id, description="a", status=TaskStatus.COMPLETED,
        output={"result": "already done"},
    )
    blocked = _blocked_task(workflow_id, dependencies=["a"])
    repository.save_tasks(db_session, [completed, blocked])
    # save_tasks is for a task's *initial* creation and doesn't copy output --
    # write it explicitly to set up "already completed" seed state.
    repository.update_task(db_session, completed)

    replan_for_missing_prerequisite(db_session, [completed, blocked], blocked, RuleBasedPlanner())

    refreshed = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert refreshed["a"].status == TaskStatus.COMPLETED
    assert refreshed["a"].output == {"result": "already done"}
    # The original dependency on "a" is preserved alongside the new one.
    assert set(blocked.dependencies) == {"a", "b_prereq_0"}
