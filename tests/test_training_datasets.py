from __future__ import annotations

import random

from runtime.state import repository
from runtime.training.datasets import generate_cold_start_workflow, run_cold_start_benchmark


def test_generate_cold_start_workflow_creates_independent_capability_tasks(db_session):
    workflow_id = generate_cold_start_workflow(db_session, 5)

    tasks = repository.get_tasks(db_session, workflow_id)
    assert len(tasks) == 5
    assert all(t.dependencies == [] for t in tasks)
    assert all(t.input["capability"] == "lookup" for t in tasks)


def test_run_cold_start_benchmark_produces_a_labeled_outcome_per_task(db_session):
    # lookup_fast/lookup_careful are deliberately unreliable (Section 15's
    # Tool A/B example), so some individual tasks are *expected* to fail --
    # the workflow's own status isn't the point here, a labeled outcome
    # (success or failure) for every task is.
    workflow_id = run_cold_start_benchmark(
        db_session, 10, explore_probability=0.5, rng=random.Random(0)
    )

    dataset = repository.get_routing_dataset(db_session, workflow_id=workflow_id)
    assert len(dataset) == 10
    assert all(row["capability"] == "lookup" for row in dataset)
    assert all(isinstance(row["success"], bool) for row in dataset)


def test_exploration_surfaces_the_non_default_tool_given_enough_tasks(db_session):
    # Default weights always pick lookup_fast with no history (see
    # test_routing_heuristic.py); with exploration on and enough tasks, the
    # dataset should contain outcomes for lookup_careful too -- the actual
    # point of running this as a "cold start" instead of plain routing.
    workflow_id = run_cold_start_benchmark(
        db_session, 30, explore_probability=0.5, rng=random.Random(0)
    )

    dataset = repository.get_routing_dataset(db_session, workflow_id=workflow_id)
    tools_seen = {row["selected_tool"] for row in dataset}
    assert tools_seen == {"lookup_fast", "lookup_careful"}
