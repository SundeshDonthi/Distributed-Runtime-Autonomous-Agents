from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.routing.neural import NeuralScorer
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository

PREFERS_CAREFUL = NeuralScorer(
    model_version="test-prefers-careful",
    feature_version="features-v1",
    training_dataset_version="synthetic",
    weights=[10.0, 0.0, 0.0, 0.0, 0.0],
    bias=-5.0,
    feature_means=[0.0, 0.0, 0.0, 0.0, 0.0],
    feature_stds=[1.0, 1.0, 1.0, 1.0, 1.0],
)


def test_active_routing_mode_decides_through_a_full_workflow_run(db_session):
    workflow_id = "wf_active_routing"
    repository.create_workflow(db_session, workflow_id, "active routing test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"capability": "lookup", "args": {"query": "x"}},
    )
    repository.save_tasks(db_session, [task])

    status = run_workflow(
        db_session, workflow_id, router_model=PREFERS_CAREFUL, routing_mode="active"
    )

    final = repository.get_tasks(db_session, workflow_id)[0]
    assert final.status == TaskStatus.COMPLETED
    assert final.input["tool"] == "lookup_careful"

    decisions = repository.get_routing_decisions_for_workflow(db_session, workflow_id)
    assert len(decisions) == 1
    assert decisions[0]["model_version"] == "test-prefers-careful"
    assert decisions[0]["fallback_reason"] is None


def test_shadow_routing_mode_does_not_change_the_live_workflows_behavior(db_session):
    workflow_id = "wf_shadow_routing"
    repository.create_workflow(db_session, workflow_id, "shadow routing test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"capability": "lookup", "args": {"query": "x"}},
    )
    repository.save_tasks(db_session, [task])

    run_workflow(db_session, workflow_id, router_model=PREFERS_CAREFUL, routing_mode="shadow")

    final = repository.get_tasks(db_session, workflow_id)[0]
    # Heuristic still decided (default weights favor lookup_fast).
    assert final.input["tool"] == "lookup_fast"

    decisions = repository.get_routing_decisions_for_workflow(db_session, workflow_id)
    assert decisions[0]["model_version"] == "heuristic-v1"
    neural_scores = {c["tool_name"]: c["neural_score"] for c in decisions[0]["candidates"]}
    assert neural_scores["lookup_careful"] is not None
