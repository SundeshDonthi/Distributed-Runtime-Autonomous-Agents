from __future__ import annotations

import random

import pytest

from runtime.models.task import Task
from runtime.routing.heuristic import DEFAULT_WEIGHTS, build_feature_vector, rank_candidates_with_details
from runtime.routing.neural import predict_proba
from runtime.state import repository
from runtime.tools.registry import get_tool_metadata
from runtime.training.datasets import run_cold_start_benchmark
from runtime.training.evaluate import (
    benchmark_routing_overhead,
    compare_policy_success_rates,
    evaluate_against_heuristic,
)
from runtime.training.train import InsufficientTrainingDataError, train_router_model


def _seed_lookup_decisions(session, n: int, seed: int = 0) -> None:
    """Directly writes `n` synthetic, labeled routing decisions for the
    "lookup" capability, with outcomes drawn from each candidate's *true*
    simulated reliability -- bypasses the scheduler/Redis/worker pipeline
    entirely (train/evaluate only ever read `routing_decisions`/`outcomes`,
    so they don't care how those rows were produced), which is both correct
    and far faster than re-running real dispatch for every test.
    """
    rng = random.Random(seed)
    weights_dict = {
        "capability_match": DEFAULT_WEIGHTS.capability_match,
        "reliability": DEFAULT_WEIGHTS.reliability,
        "latency": DEFAULT_WEIGHTS.latency,
        "cost": DEFAULT_WEIGHTS.cost,
    }
    for i in range(n):
        workflow_id = f"wf_{i}"
        repository.create_workflow(session, workflow_id, "synthetic")
        task_id = f"{workflow_id}_t0"
        repository.save_tasks(session, [Task(id=task_id, workflow_id=workflow_id, description="t")])

        ranked = rank_candidates_with_details("lookup")
        chosen_tool, _score, _features = rng.choice(ranked)  # uniform "exploration" over both candidates
        decision_id = repository.create_routing_decision(
            session=session,
            task_id=task_id,
            workflow_id=workflow_id,
            capability="lookup",
            ranked_candidates=[(t.name, s, f) for t, s, f in ranked],
            selected_tool=chosen_tool.name,
            weights=weights_dict,
            was_exploration=True,
        )
        success = rng.random() < chosen_tool.reliability
        repository.create_routing_outcome(
            session=session,
            decision_id=decision_id,
            success=success,
            latency_ms=chosen_tool.base_latency_seconds * 1000.0,
            error=None if success else "simulated failure",
        )
    session.commit()


@pytest.fixture()
def seeded_dataset(db_session):
    _seed_lookup_decisions(db_session, n=60)
    return db_session


def test_train_router_model_on_insufficient_data_raises(db_session):
    _seed_lookup_decisions(db_session, n=2)
    with pytest.raises(InsufficientTrainingDataError):
        train_router_model(db_session)


def test_train_router_model_produces_a_usable_scorer(seeded_dataset):
    scorer, stats = train_router_model(seeded_dataset)

    assert stats["n_examples"] > 0
    assert stats["n_positive"] + stats["n_negative"] == stats["n_examples"]
    assert len(scorer.weights) == 5
    assert scorer.model_version.startswith("neural-v1-")

    p = predict_proba(scorer, build_feature_vector(get_tool_metadata("lookup_fast")))
    assert 0.0 <= p <= 1.0


def test_train_router_model_is_reproducible_given_the_same_seed(seeded_dataset):
    scorer1, _ = train_router_model(seeded_dataset, seed=1)
    scorer2, _ = train_router_model(seeded_dataset, seed=1)
    assert scorer1.weights == scorer2.weights
    assert scorer1.bias == scorer2.bias
    assert scorer1.training_dataset_version == scorer2.training_dataset_version


def test_evaluate_against_heuristic_reports_calibration_and_agreement(seeded_dataset):
    scorer, _ = train_router_model(seeded_dataset)
    result = evaluate_against_heuristic(seeded_dataset, scorer)

    assert result["split"] == "test"
    assert result["n"] >= 0
    assert "ece" in result["calibration"]
    if result["n"] > 0:
        assert 0.0 <= result["agreement_rate_with_heuristic"] <= 1.0
        assert 0.0 <= result["accuracy_at_threshold_0.5"] <= 1.0


def test_compare_policy_success_rates_reports_both_policies_picks(seeded_dataset):
    scorer, _ = train_router_model(seeded_dataset)
    result = compare_policy_success_rates(seeded_dataset, scorer)

    assert result["heuristic_pick"] in {"lookup_fast", "lookup_careful"}
    assert result["neural_pick"] in {"lookup_fast", "lookup_careful"}
    assert result["n_held_out_decisions"] >= 0


def test_benchmark_routing_overhead_reports_positive_timings(seeded_dataset):
    scorer, _ = train_router_model(seeded_dataset)
    features = [build_feature_vector(get_tool_metadata("lookup_fast"))]
    result = benchmark_routing_overhead(scorer, features, iterations=50)

    assert result["heuristic_seconds_per_decision"] > 0
    assert result["neural_seconds_per_candidate"] > 0


def test_cold_start_benchmark_data_also_trains_successfully(db_session):
    # One slower, more "realistic" test that actually exercises the full
    # scheduler/dispatch pipeline end to end, to catch anything the
    # direct-seed shortcut above might paper over. Several separate
    # workflows, not one big one: splits are assigned per-workflow, so a
    # single workflow's decisions are an all-or-nothing coin flip into
    # train/val/test -- spreading across many workflows is what actually
    # gives ~70% of decisions a good chance of landing in "train".
    rng = random.Random(0)
    for _ in range(15):
        run_cold_start_benchmark(db_session, 3, explore_probability=0.5, rng=rng)
    scorer, stats = train_router_model(db_session)
    assert stats["n_examples"] > 0
