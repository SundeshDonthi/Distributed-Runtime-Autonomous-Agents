"""The actual Phase 7 demonstration: generates a cold-start routing dataset,
trains the neural scorer on its "train" split, evaluates it against the
heuristic baseline on "test" (Section 15J: "evaluation compares neural
routing against heuristic baselines on held-out workflows"), and reports
real, measured numbers -- not illustrative ones (Section 15K). Saves the
trained model to `router_models` in "shadow" status; promoting it to
"active" is a separate, deliberate step (the `POST /admin/router/models/
{version}/activate` endpoint), not something this script does on its own.

Run:
    python -m benchmarks.train_and_evaluate_router
"""
from __future__ import annotations

import random

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from runtime.routing.heuristic import build_feature_vector
from runtime.state import repository
from runtime.state.orm import Base
from runtime.tools.registry import get_tool_metadata
from runtime.training.calibrate import is_calibrated
from runtime.training.datasets import run_cold_start_benchmark
from runtime.training.evaluate import (
    benchmark_routing_overhead,
    compare_policy_success_rates,
    evaluate_against_heuristic,
)
from runtime.training.train import train_router_model

N_WORKFLOWS = 80
TASKS_PER_WORKFLOW = 3
SEED = 0


def main() -> None:
    engine = create_engine("sqlite:///./router_training.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    rng = random.Random(SEED)

    print(f"Generating cold-start dataset: {N_WORKFLOWS} workflows x {TASKS_PER_WORKFLOW} tasks...")
    for _ in range(N_WORKFLOWS):
        run_cold_start_benchmark(session, TASKS_PER_WORKFLOW, explore_probability=0.4, rng=rng)
    session.commit()

    scorer, train_stats = train_router_model(session, seed=SEED)
    print()
    print(
        f"Trained {scorer.model_version} on {train_stats['n_examples']} examples "
        f"({train_stats['n_positive']} positive, {train_stats['n_negative']} negative)"
    )
    feature_names = [
        "static_reliability",
        "static_latency_seconds",
        "cost_per_call",
        "observed_reliability",
        "observed_latency_seconds",
    ]
    print(f"Learned weights: {dict(zip(feature_names, scorer.weights))}")
    print(f"Bias: {scorer.bias:.4f}")

    evaluation = evaluate_against_heuristic(session, scorer, split="test", seed=SEED)
    print()
    print(f"Held-out evaluation ({evaluation['split']}, n={evaluation['n']}):")
    print(f"  Calibration ECE:               {evaluation['calibration']['ece']}")
    print(f"  Brier score:                   {evaluation['calibration']['brier_score']}")
    print(f"  Is calibrated:                 {evaluation['is_calibrated']}")
    print(f"  Accuracy @ 0.5 threshold:      {evaluation['accuracy_at_threshold_0.5']}")
    print(f"  Agreement rate with heuristic: {evaluation['agreement_rate_with_heuristic']}")

    comparison = compare_policy_success_rates(session, scorer, split="test", seed=SEED)
    print()
    print("Policy comparison on held-out data:")
    print(f"  Heuristic would pick: {comparison['heuristic_pick']!s:16s} "
          f"empirical success rate: {comparison['heuristic_pick_empirical_success_rate']}")
    print(f"  Neural would pick:    {comparison['neural_pick']!s:16s} "
          f"empirical success rate: {comparison['neural_pick_empirical_success_rate']}")

    sample_features = [build_feature_vector(get_tool_metadata("lookup_fast"))]
    overhead = benchmark_routing_overhead(scorer, sample_features, iterations=2000)
    print()
    print("Routing overhead (Section 15C.2/15I.2):")
    print(f"  Heuristic: {overhead['heuristic_seconds_per_decision'] * 1e6:.2f} us/decision")
    print(f"  Neural:    {overhead['neural_seconds_per_candidate'] * 1e6:.2f} us/candidate")

    status = "shadow"
    repository.save_router_model(
        session,
        model_version=scorer.model_version,
        feature_version=scorer.feature_version,
        training_dataset_version=scorer.training_dataset_version,
        artifact=scorer.to_dict(),
        status=status,
    )
    repository.set_router_model_calibration(session, scorer.model_version, evaluation["is_calibrated"])
    session.commit()
    print()
    print(f"Saved {scorer.model_version} to router_models with status={status!r}.")


if __name__ == "__main__":
    main()
