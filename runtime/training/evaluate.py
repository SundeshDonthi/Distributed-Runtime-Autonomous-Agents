from __future__ import annotations

import time

from sqlalchemy.orm import Session

from runtime.routing.heuristic import build_feature_vector, rank_candidates_with_scores, select_tool
from runtime.routing.neural import NeuralScorer, predict_proba
from runtime.tools.registry import tools_for_capability
from runtime.training.calibrate import compute_calibration, is_calibrated
from runtime.training.datasets import export_training_rows, selected_candidate_features


def evaluate_against_heuristic(
    session: Session,
    scorer: NeuralScorer,
    *,
    capability: str = "lookup",
    split: str = "test",
    seed: int = 0,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> dict:
    """Section 15I's evaluation, scoped to what's actually measurable with
    our data: calibration of the model's own predictions against real
    outcomes, how often its top pick agrees with the heuristic's, and simple
    accuracy at a 0.5 threshold on the candidate that was actually dispatched.
    Section 15J's own definition of done for this phase -- "evaluation
    compares neural routing against heuristic baselines on held-out
    workflows" -- is exactly what this does; it does not evaluate against the
    *chosen* tool's counterfactual alternatives, since Section 15D.2 flags
    those as having unknown outcomes outside of controlled exploration.
    """
    rows = export_training_rows(session, seed=seed, train_frac=train_frac, val_frac=val_frac)
    held_out = [r for r in rows if r["split"] == split and r["capability"] == capability]

    predictions: list[float] = []
    labels: list[bool] = []
    agreements = 0
    scored_rows = 0

    for row in held_out:
        selected_features = selected_candidate_features(row)
        if selected_features is None:
            continue
        scored_rows += 1
        predictions.append(predict_proba(scorer, selected_features))
        labels.append(row["success"])

        neural_ranked = sorted(
            row["candidates"], key=lambda c: predict_proba(scorer, c["features"]), reverse=True
        )
        heuristic_ranked = sorted(row["candidates"], key=lambda c: c["score"], reverse=True)
        if neural_ranked[0]["tool_name"] == heuristic_ranked[0]["tool_name"]:
            agreements += 1

    calibration = compute_calibration(predictions, labels)
    accuracy = (
        sum(1 for p, y in zip(predictions, labels) if (p >= 0.5) == y) / len(predictions)
        if predictions
        else None
    )

    return {
        "split": split,
        "capability": capability,
        "n": scored_rows,
        "calibration": calibration,
        "is_calibrated": is_calibrated(calibration),
        "agreement_rate_with_heuristic": agreements / scored_rows if scored_rows else None,
        "accuracy_at_threshold_0.5": accuracy,
    }


def _empirical_success_rate(rows: list[dict], tool_name: str) -> float | None:
    calls = [r for r in rows if r["selected_tool"] == tool_name]
    if not calls:
        return None
    return sum(1 for r in calls if r["success"]) / len(calls)


def compare_policy_success_rates(
    session: Session,
    scorer: NeuralScorer,
    *,
    capability: str = "lookup",
    split: str = "test",
    seed: int = 0,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> dict:
    """Section 15I.2's "validated workflow completion rate," the metric that
    actually answers "is neural routing better," as opposed to
    `agreement_rate_with_heuristic` above, which only says whether the two
    policies pick the *same* thing -- not which pick is better (and Section
    15D.2 is explicit that an unchosen candidate's outcome is usually
    unknown outside controlled exploration, so "they disagree" isn't itself
    bad). This estimates each policy's default (no observed-history) pick,
    then reports that tool's empirical success rate from real held-out
    outcomes -- including from decisions where it was reached via
    exploration, not just the policy's own greedy choice, since that's where
    most of a cold-start tool's outcome history comes from in the first
    place.
    """
    rows = export_training_rows(session, seed=seed, train_frac=train_frac, val_frac=val_frac)
    held_out = [r for r in rows if r["split"] == split and r["capability"] == capability]

    candidates = tools_for_capability(capability)
    heuristic_pick = select_tool(capability).name
    neural_scores = {t.name: predict_proba(scorer, build_feature_vector(t)) for t in candidates}
    neural_pick = max(neural_scores, key=neural_scores.get)

    return {
        "n_held_out_decisions": len(held_out),
        "heuristic_pick": heuristic_pick,
        "heuristic_pick_empirical_success_rate": _empirical_success_rate(held_out, heuristic_pick),
        "neural_pick": neural_pick,
        "neural_pick_empirical_success_rate": _empirical_success_rate(held_out, neural_pick),
    }


def benchmark_routing_overhead(
    scorer: NeuralScorer, candidate_features: list[dict], iterations: int = 1000
) -> dict:
    """Section 15C.2: "benchmark routing overhead against the time saved by
    better choices"; Section 15I.2: "routing overhead." Compares per-call
    latency of the heuristic's scoring loop against the neural scorer's --
    both are pure in-process Python, so this isolates model overhead from
    anything like a network call or a real model-serving round trip.
    """
    from runtime.tools.registry import tools_for_capability

    lookup_tools = tools_for_capability("lookup")

    start = time.perf_counter()
    for _ in range(iterations):
        rank_candidates_with_scores("lookup")
    heuristic_seconds = (time.perf_counter() - start) / iterations

    start = time.perf_counter()
    for _ in range(iterations):
        for features in candidate_features:
            predict_proba(scorer, features)
    neural_seconds = (time.perf_counter() - start) / (iterations * max(len(candidate_features), 1))

    return {
        "heuristic_seconds_per_decision": heuristic_seconds,
        "neural_seconds_per_candidate": neural_seconds,
        "n_candidates_scored": len(lookup_tools),
    }
