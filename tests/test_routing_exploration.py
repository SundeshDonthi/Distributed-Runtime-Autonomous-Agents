from __future__ import annotations

import random

from runtime.models.task import Task
from runtime.routing.dispatch import resolve_task_tool
from runtime.state import repository


def _capability_task(session, task_id: str) -> Task:
    workflow_id = f"wf_{task_id}"
    repository.create_workflow(session, workflow_id, "exploration test")
    task = Task(
        id=task_id,
        workflow_id=workflow_id,
        description="t",
        input={"capability": "lookup", "args": {}},
    )
    repository.save_tasks(session, [task])
    return task


def test_zero_explore_probability_never_deviates_from_greedy(db_session):
    rng = random.Random(0)
    for i in range(20):
        task = _capability_task(db_session, f"t{i}")
        decision_id = resolve_task_tool(
            db_session, task, explore_probability=0.0, rng=rng
        )
        decision = repository.get_routing_decision(db_session, decision_id)
        assert decision["was_exploration"] is False
        assert decision["selected_tool"] == decision["candidates"][0]["tool_name"]


def test_full_exploration_eventually_picks_the_non_greedy_candidate(db_session):
    # explore_probability=1.0 means every decision goes through the random
    # candidate picker; with only 2 candidates and enough draws, both should
    # show up -- proves the mechanism actually samples from all candidates,
    # not just a dressed-up no-op.
    rng = random.Random(0)
    selected = set()
    for i in range(20):
        task = _capability_task(db_session, f"t{i}")
        decision_id = resolve_task_tool(
            db_session, task, explore_probability=1.0, rng=rng
        )
        decision = repository.get_routing_decision(db_session, decision_id)
        assert decision["was_exploration"] is True
        selected.add(decision["selected_tool"])

    assert selected == {"lookup_fast", "lookup_careful"}


def test_single_candidate_capability_is_never_flagged_as_exploration(db_session):
    # "echo" has exactly one registered tool -- there's nothing to explore
    # into, so the exploration branch must be a no-op rather than erroring.
    workflow_id = "wf_single"
    repository.create_workflow(db_session, workflow_id, "single candidate test")
    task = Task(
        id="t_single",
        workflow_id=workflow_id,
        description="t",
        input={"capability": "echo", "args": {}},
    )
    repository.save_tasks(db_session, [task])

    decision_id = resolve_task_tool(
        db_session, task, explore_probability=1.0, rng=random.Random(0)
    )

    decision = repository.get_routing_decision(db_session, decision_id)
    assert decision["was_exploration"] is False
    assert decision["selected_tool"] == "echo"
