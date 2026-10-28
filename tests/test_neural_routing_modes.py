from __future__ import annotations

from runtime.models.task import Task
from runtime.routing.dispatch import resolve_task_tool
from runtime.routing.neural import NeuralScorer
from runtime.state import repository

# A scorer that strongly prefers lookup_careful: static_reliability is its
# only input with a large positive weight, everything else zeroed out --
# makes the "which tool did neural actually pick" assertions deterministic
# without needing a real trained model in every test.
PREFERS_CAREFUL = NeuralScorer(
    model_version="test-prefers-careful",
    feature_version="features-v1",
    training_dataset_version="synthetic",
    weights=[10.0, 0.0, 0.0, 0.0, 0.0],
    bias=-5.0,
    feature_means=[0.0, 0.0, 0.0, 0.0, 0.0],
    feature_stds=[1.0, 1.0, 1.0, 1.0, 1.0],
)

LOW_CONFIDENCE = NeuralScorer(
    model_version="test-low-confidence",
    feature_version="features-v1",
    training_dataset_version="synthetic",
    weights=[0.0, 0.0, 0.0, 0.0, 0.0],
    bias=0.0,  # sigmoid(0) == 0.5 for every candidate, regardless of features
    feature_means=[0.0, 0.0, 0.0, 0.0, 0.0],
    feature_stds=[1.0, 1.0, 1.0, 1.0, 1.0],
)

STALE_FEATURE_VERSION = NeuralScorer(
    model_version="test-stale",
    feature_version="features-v0-does-not-exist",
    training_dataset_version="synthetic",
    weights=[10.0, 0.0, 0.0, 0.0, 0.0],
    bias=-5.0,
    feature_means=[0.0, 0.0, 0.0, 0.0, 0.0],
    feature_stds=[1.0, 1.0, 1.0, 1.0, 1.0],
)


def _task(session, task_id: str) -> Task:
    workflow_id = f"wf_{task_id}"
    repository.create_workflow(session, workflow_id, "routing mode test")
    task = Task(
        id=task_id, workflow_id=workflow_id, description="t",
        input={"capability": "lookup", "args": {"query": "x"}},
    )
    repository.save_tasks(session, [task])
    return task


def test_heuristic_mode_ignores_a_supplied_model_entirely(db_session):
    task = _task(db_session, "t1")
    decision_id = resolve_task_tool(
        db_session, task, router_model=PREFERS_CAREFUL, routing_mode="heuristic"
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    assert decision["model_version"] == "heuristic-v1"
    assert decision["fallback_reason"] is None
    assert all(c["neural_score"] is None for c in decision["candidates"])
    # Default heuristic weights favor lookup_fast (see test_routing_heuristic.py).
    assert task.input["tool"] == "lookup_fast"


def test_shadow_mode_logs_neural_scores_without_changing_the_selection(db_session):
    task = _task(db_session, "t2")
    decision_id = resolve_task_tool(
        db_session, task, router_model=PREFERS_CAREFUL, routing_mode="shadow"
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    # Heuristic still decided...
    assert decision["model_version"] == "heuristic-v1"
    assert task.input["tool"] == "lookup_fast"
    # ...but every candidate's neural score was computed and logged.
    neural_scores = {c["tool_name"]: c["neural_score"] for c in decision["candidates"]}
    assert neural_scores["lookup_careful"] is not None
    assert neural_scores["lookup_fast"] is not None
    assert neural_scores["lookup_careful"] > neural_scores["lookup_fast"]


def test_active_mode_uses_the_neural_pick_when_confident(db_session):
    task = _task(db_session, "t3")
    decision_id = resolve_task_tool(
        db_session,
        task,
        router_model=PREFERS_CAREFUL,
        routing_mode="active",
        confidence_threshold=0.6,
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    assert task.input["tool"] == "lookup_careful"
    assert decision["model_version"] == "test-prefers-careful"
    assert decision["fallback_reason"] is None


def test_active_mode_falls_back_to_heuristic_on_low_confidence(db_session):
    task = _task(db_session, "t4")
    decision_id = resolve_task_tool(
        db_session,
        task,
        router_model=LOW_CONFIDENCE,
        routing_mode="active",
        confidence_threshold=0.6,
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    assert task.input["tool"] == "lookup_fast"  # heuristic's own default pick
    assert decision["model_version"] == "heuristic-v1"
    assert decision["fallback_reason"] == "low_confidence"


def test_active_mode_falls_back_to_heuristic_when_no_model_is_supplied(db_session):
    task = _task(db_session, "t5")
    decision_id = resolve_task_tool(db_session, task, router_model=None, routing_mode="active")
    decision = repository.get_routing_decision(db_session, decision_id)

    assert task.input["tool"] == "lookup_fast"
    assert decision["model_version"] == "heuristic-v1"
    assert decision["fallback_reason"] == "model_missing"


def test_active_mode_falls_back_on_stale_feature_version(db_session):
    task = _task(db_session, "t6")
    decision_id = resolve_task_tool(
        db_session, task, router_model=STALE_FEATURE_VERSION, routing_mode="active"
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    assert task.input["tool"] == "lookup_fast"
    assert decision["fallback_reason"] == "feature_version_mismatch_or_inference_error"
    # A stale-schema model's numbers shouldn't even be logged -- they'd be
    # silently meaningless against today's feature layout.
    assert all(c["neural_score"] is None for c in decision["candidates"])


def test_exploration_overrides_an_active_neural_pick_and_clears_fallback_reason(db_session):
    import random

    task = _task(db_session, "t7")
    # explore_probability=1.0 guarantees the random mechanism fires.
    decision_id = resolve_task_tool(
        db_session,
        task,
        router_model=PREFERS_CAREFUL,
        routing_mode="active",
        explore_probability=1.0,
        rng=random.Random(0),
    )
    decision = repository.get_routing_decision(db_session, decision_id)

    assert decision["was_exploration"] is True
    assert decision["fallback_reason"] is None
    assert decision["model_version"] == "heuristic-v1"
