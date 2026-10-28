from __future__ import annotations

import uuid

import pytest

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.planner.planner import RuleBasedPlanner
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.tools.registry import MissingPrerequisiteError, ToolMetadata, register_tool, unregister_tool


def test_replan_unblocks_a_task_and_reuses_completed_work(db_session):
    # Section 5.10's own example: A -> B -> C, B discovers it needs more
    # info, the graph becomes A -> B -> D -> C without rerunning A.
    workflow_id = "wf_abc"
    repository.create_workflow(db_session, workflow_id, "replan scenario")
    topic = f"topic-{uuid.uuid4().hex[:8]}"  # unique: research_step's "seen" state is process-wide
    tasks = [
        Task(id="A", workflow_id=workflow_id, description="A", input={"tool": "echo", "args": {"text": "a"}}),
        Task(id="B", workflow_id=workflow_id, description="B", dependencies=["A"],
             input={"tool": "research_step", "args": {"topic": topic}}),
        Task(id="C", workflow_id=workflow_id, description="C", dependencies=["B"],
             input={"tool": "uppercase", "args": {"text": "done"}}),
    ]
    repository.save_tasks(db_session, tasks)

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.COMPLETED
    final = {t.id: t for t in repository.get_tasks(db_session, workflow_id)}
    assert final["A"].status == TaskStatus.COMPLETED
    assert final["B"].status == TaskStatus.COMPLETED
    assert final["B"].replan_count == 1
    assert final["C"].status == TaskStatus.COMPLETED

    prereq_ids = [t for t in final if t not in ("A", "B", "C")]
    assert len(prereq_ids) == 1
    prereq = final[prereq_ids[0]]
    assert prereq.status == TaskStatus.COMPLETED
    assert prereq.id in final["B"].dependencies

    # Ordering: A before B's (successful, second) attempt before C.
    assert final["A"].completed_at <= prereq.started_at
    assert prereq.completed_at <= final["B"].started_at
    assert final["B"].completed_at <= final["C"].started_at

    wf = repository.get_workflow(db_session, workflow_id)
    assert wf.plan_version == 2
    events = repository.get_replan_events(db_session, workflow_id)
    assert len(events) == 1
    assert events[0]["blocked_task_id"] == "B"


@pytest.fixture()
def always_needs_prerequisite_tool():
    name = "always_needs_prerequisite"

    def fn(args: dict) -> dict:
        raise MissingPrerequisiteError("perpetually missing")

    register_tool(
        ToolMetadata(name=name, capability="never_satisfied", fn=fn, base_latency_seconds=0.0, reliability=1.0, cost_per_call=0.0)
    )
    yield name
    unregister_tool(name)


class _DeterministicSubplanPlanner(RuleBasedPlanner):
    """Inserts a 100%-reliable `echo` prerequisite instead of the default
    `lookup` capability -- this test only cares about the replan *count*
    being bounded, not about lookup_fast/careful's own simulated
    unreliability potentially adding retries along the way.
    """

    def generate_subplan(self, workflow_id, blocked_task_id, replan_index, context):
        return [
            Task(
                id=f"{blocked_task_id}_prereq_{replan_index}",
                workflow_id=workflow_id,
                description=f"prereq for {context}",
                input={"tool": "echo", "args": {"text": context}},
            )
        ]


def test_replan_is_bounded_and_eventually_gives_up(db_session, always_needs_prerequisite_tool):
    workflow_id = "wf_unbounded"
    repository.create_workflow(db_session, workflow_id, "bounded replan test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"tool": always_needs_prerequisite_tool, "args": {}},
        max_replans=2,
    )
    repository.save_tasks(db_session, [task])

    status = run_workflow(db_session, workflow_id, planner=_DeterministicSubplanPlanner())

    assert status == WorkflowStatus.FAILED
    final = repository.get_tasks(db_session, workflow_id)
    task_final = next(t for t in final if t.id == "t1")
    assert task_final.status == TaskStatus.FAILED
    assert task_final.replan_count == 2
    assert "replan budget exhausted" in task_final.error

    # Each failed attempt inserted its own prerequisite task before giving up.
    assert len(final) == 1 + 2

    wf = repository.get_workflow(db_session, workflow_id)
    assert wf.plan_version == 3  # 1 + one bump per successful replan
