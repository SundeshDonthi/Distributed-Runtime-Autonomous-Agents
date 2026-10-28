from __future__ import annotations

import random

import pytest

from runtime.routing.neural import (
    NeuralScorer,
    predict_proba,
    raw_feature_vector,
    train_logistic_regression,
)


def _feature(reliability: float, latency: float = 0.1, cost: float = 0.0, observed: bool = False) -> dict:
    return {
        "static_reliability": reliability,
        "static_latency_seconds": latency,
        "cost_per_call": cost,
        "observed_reliability": reliability if observed else None,
        "observed_latency_seconds": latency if observed else None,
    }


def _synthetic_examples(n: int, seed: int = 0) -> list[tuple[dict, bool]]:
    rng = random.Random(seed)
    examples = []
    for _ in range(n):
        reliability = rng.uniform(0.4, 0.99)
        success = rng.random() < reliability
        examples.append((_feature(reliability, rng.uniform(0.01, 0.5), rng.uniform(0.0, 0.02)), success))
    return examples


def test_raw_feature_vector_imputes_observed_fields_from_static_at_cold_start():
    features = _feature(0.9, 0.2, 0.01, observed=False)
    vector = raw_feature_vector(features)
    # observed_reliability/observed_latency_seconds are None -> fall back to
    # the static priors, same imputation the heuristic itself uses.
    assert vector == [0.9, 0.2, 0.01, 0.9, 0.2]


def test_raw_feature_vector_uses_observed_values_when_present():
    features = _feature(0.9, 0.2, 0.01, observed=True)
    features["observed_reliability"] = 0.5
    features["observed_latency_seconds"] = 0.4
    vector = raw_feature_vector(features)
    assert vector == [0.9, 0.2, 0.01, 0.5, 0.4]


def test_training_on_empty_examples_raises():
    with pytest.raises(ValueError):
        train_logistic_regression([], model_version="m", feature_version="v1", training_dataset_version="d")


def test_trained_model_assigns_higher_probability_to_more_reliable_candidates():
    examples = _synthetic_examples(400)
    scorer = train_logistic_regression(
        examples, model_version="m1", feature_version="v1", training_dataset_version="d1"
    )

    low = predict_proba(scorer, _feature(0.5))
    high = predict_proba(scorer, _feature(0.95))
    assert high > low
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0


def test_higher_latency_and_cost_reduce_predicted_success_probability_less_than_reliability():
    # Not a hard requirement that latency/cost even matter for *success*
    # (they're real costs but don't make a call more or less likely to
    # succeed in our simulated tools) -- this just checks the model doesn't
    # blow up or produce nonsense for varied inputs.
    examples = _synthetic_examples(300, seed=1)
    scorer = train_logistic_regression(
        examples, model_version="m2", feature_version="v1", training_dataset_version="d2"
    )
    for reliability in (0.4, 0.6, 0.8, 0.95):
        for latency in (0.01, 0.1, 0.5):
            p = predict_proba(scorer, _feature(reliability, latency))
            assert 0.0 <= p <= 1.0


def test_scorer_round_trips_through_dict_serialization():
    examples = _synthetic_examples(100)
    scorer = train_logistic_regression(
        examples, model_version="m3", feature_version="v1", training_dataset_version="d3"
    )
    restored = NeuralScorer.from_dict(scorer.to_dict())
    features = _feature(0.8)
    assert predict_proba(scorer, features) == predict_proba(restored, features)
