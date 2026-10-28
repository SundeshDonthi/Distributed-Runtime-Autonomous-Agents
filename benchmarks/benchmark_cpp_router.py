"""Phase 9's actual claim, measured rather than assumed (Section 16/15K):
does the C++ kernel in `runtime.routing._native` genuinely run faster than
`runtime.routing.neural`'s pure-Python reference implementation, and by how
much? Compares both backends on the same synthetic dataset for training
(the real, scaling workload -- O(epochs * n * n_features)) and for batched
inference (included for completeness, but Phase 7's own benchmark already
found a single candidate's inference indistinguishable from noise at ~1us,
so a small/moderate win here is the honest expectation, not a dramatic one).

Run:
    python -m benchmarks.benchmark_cpp_router
"""
from __future__ import annotations

import random
import time

from runtime.routing.neural import native_backend_available, predict_proba_batch, train_logistic_regression

N_EXAMPLES = 4000
EPOCHS = 300
N_INFERENCE_CALLS = 20000
SEED = 0


def _feature(reliability: float, latency: float, cost: float) -> dict:
    return {
        "static_reliability": reliability,
        "static_latency_seconds": latency,
        "cost_per_call": cost,
        "observed_reliability": reliability,
        "observed_latency_seconds": latency,
    }


def _synthetic_examples(n: int, seed: int) -> list[tuple[dict, bool]]:
    rng = random.Random(seed)
    examples = []
    for _ in range(n):
        reliability = rng.uniform(0.3, 0.99)
        latency = rng.uniform(0.01, 0.9)
        cost = rng.uniform(0.0, 0.05)
        success = rng.random() < reliability
        examples.append((_feature(reliability, latency, cost), success))
    return examples


def _timed(label: str, fn) -> tuple[float, object]:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(f"  {label}: {elapsed:.4f}s")
    return elapsed, result


def main() -> None:
    if not native_backend_available():
        print(
            "runtime.routing._native isn't built -- run "
            "`python setup.py build_ext --inplace` first."
        )
        return

    examples = _synthetic_examples(N_EXAMPLES, SEED)
    print(f"Training on {N_EXAMPLES} examples for {EPOCHS} epochs (5 features)...")

    python_time, python_scorer = _timed(
        "python backend",
        lambda: train_logistic_regression(
            examples, model_version="bench-python", feature_version="f", training_dataset_version="d",
            epochs=EPOCHS, backend="python",
        ),
    )
    cpp_time, cpp_scorer = _timed(
        "cpp backend   ",
        lambda: train_logistic_regression(
            examples, model_version="bench-cpp", feature_version="f", training_dataset_version="d",
            epochs=EPOCHS, backend="cpp",
        ),
    )
    print(f"  speedup: {python_time / cpp_time:.1f}x")
    max_weight_diff = max(
        abs(a - b) for a, b in zip(python_scorer.weights, cpp_scorer.weights)
    )
    print(f"  max |weight difference| between backends: {max_weight_diff:.2e} (should be ~0)")

    print()
    print(f"Batched inference over {N_INFERENCE_CALLS} candidates...")
    feature_dicts = [f for f, _ in _synthetic_examples(N_INFERENCE_CALLS, SEED + 1)]
    py_infer_time, py_probs = _timed(
        "python backend", lambda: predict_proba_batch(python_scorer, feature_dicts, backend="python")
    )
    cpp_infer_time, cpp_probs = _timed(
        "cpp backend   ", lambda: predict_proba_batch(python_scorer, feature_dicts, backend="cpp")
    )
    print(f"  speedup: {py_infer_time / cpp_infer_time:.1f}x")
    max_prob_diff = max(abs(a - b) for a, b in zip(py_probs, cpp_probs))
    print(f"  max |probability difference| between backends: {max_prob_diff:.2e} (should be ~0)")


if __name__ == "__main__":
    main()
