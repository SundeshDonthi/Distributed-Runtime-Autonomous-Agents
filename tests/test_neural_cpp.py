"""Phase 9's correctness contract: the C++ kernel in `runtime.routing._native`
isn't a different model, it's the same algorithm as `runtime.routing.neural`'s
pure-Python reference implementation, moved to compiled code. Every test here
either proves the two backends agree (within floating-point tolerance -- a
different summation order between a Python generator and a C++ loop is
expected, a different *answer* is not) or proves the "optional at runtime"
fallback (Section 15A.1) actually holds when the extension isn't available.

Skipped entirely if `runtime.routing._native` hasn't been built
(`python setup.py build_ext --inplace`) -- consistent with every other
optional-dependency gate in this suite (Redis, a live Postgres).
"""
from __future__ import annotations

import random

import pytest

import runtime.routing.neural as neural
from runtime.routing.neural import (
    NeuralScorer,
    native_backend_available,
    predict_proba_batch,
    train_logistic_regression,
)

pytestmark = pytest.mark.skipif(
    not native_backend_available(),
    reason="runtime.routing._native hasn't been built -- run "
    "`python setup.py build_ext --inplace`",
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
        examples.append(
            (_feature(reliability, rng.uniform(0.01, 0.5), rng.uniform(0.0, 0.02), observed=True), success)
        )
    return examples


def test_cpp_training_matches_python_training_within_floating_point_tolerance():
    examples = _synthetic_examples(200, seed=1)

    python_scorer = train_logistic_regression(
        examples, model_version="v", feature_version="f", training_dataset_version="d",
        epochs=150, backend="python",
    )
    cpp_scorer = train_logistic_regression(
        examples, model_version="v", feature_version="f", training_dataset_version="d",
        epochs=150, backend="cpp",
    )

    assert cpp_scorer.weights == pytest.approx(python_scorer.weights, abs=1e-6)
    assert cpp_scorer.bias == pytest.approx(python_scorer.bias, abs=1e-6)
    assert cpp_scorer.feature_means == pytest.approx(python_scorer.feature_means, abs=1e-9)
    assert cpp_scorer.feature_stds == pytest.approx(python_scorer.feature_stds, abs=1e-9)


def test_cpp_training_converges_the_same_way_the_python_reference_does():
    """Not just "the two backends agree with each other" -- both must still
    produce a model that actually separates reliable from unreliable tools,
    same sanity check Phase 7's own tests already run against the Python
    path.
    """
    examples = _synthetic_examples(300, seed=2)
    scorer = train_logistic_regression(
        examples, model_version="v", feature_version="f", training_dataset_version="d",
        epochs=200, backend="cpp",
    )

    reliable = predict_proba_batch(scorer, [_feature(0.98, 0.05, 0.0, observed=True)], backend="python")[0]
    unreliable = predict_proba_batch(scorer, [_feature(0.3, 0.9, 0.02, observed=True)], backend="python")[0]
    assert reliable > unreliable


def test_cpp_batched_inference_matches_python_inference():
    examples = _synthetic_examples(100, seed=3)
    scorer = train_logistic_regression(
        examples, model_version="v", feature_version="f", training_dataset_version="d",
        epochs=100, backend="python",
    )
    feature_dicts = [f for f, _ in examples]

    python_probs = predict_proba_batch(scorer, feature_dicts, backend="python")
    cpp_probs = predict_proba_batch(scorer, feature_dicts, backend="cpp")

    assert cpp_probs == pytest.approx(python_probs, abs=1e-9)


def test_cpp_batch_preserves_input_order():
    scorer = NeuralScorer(
        model_version="v", feature_version="f", training_dataset_version="d",
        weights=[1.0, 0.0, 0.0, 0.0, 0.0], bias=0.0,
        feature_means=[0.0] * 5, feature_stds=[1.0] * 5,
    )
    low, mid, high = _feature(0.1), _feature(0.5), _feature(0.9)

    probs = predict_proba_batch(scorer, [high, low, mid], backend="cpp")
    assert probs[0] > probs[2] > probs[1]


def test_training_raises_on_empty_examples_regardless_of_backend():
    with pytest.raises(ValueError, match="empty"):
        train_logistic_regression(
            [], model_version="v", feature_version="f", training_dataset_version="d", backend="cpp"
        )


def test_requesting_cpp_backend_without_the_extension_raises_a_clear_error(monkeypatch):
    """Simulates the extension never having been built (Section 15A.1's
    "optional at runtime" -- a missing native module must fail clearly, not
    silently fall back to a different backend than the caller asked for and
    not crash with an unrelated AttributeError/ImportError deep in the call
    stack).
    """
    monkeypatch.setattr(neural, "_native_router", None)

    with pytest.raises(RuntimeError, match="build_ext"):
        train_logistic_regression(
            _synthetic_examples(10), model_version="v", feature_version="f",
            training_dataset_version="d", backend="cpp",
        )
    with pytest.raises(RuntimeError, match="build_ext"):
        predict_proba_batch(
            NeuralScorer(model_version="v", feature_version="f", training_dataset_version="d",
                         weights=[0.0] * 5, bias=0.0, feature_means=[0.0] * 5, feature_stds=[1.0] * 5),
            [_feature(0.5)],
            backend="cpp",
        )
