from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

# Section 15C.1's "compact text encoder + MLP" architecture doesn't have a
# meaningful text signal to encode at this project's current scale -- task
# descriptions are synthetic strings like "benchmark lookup call 3", not
# anything with real semantic content. At that feature richness the
# architecture honestly degenerates to logistic regression: a single linear
# layer + sigmoid, which is literally the zero-hidden-layer case of an MLP,
# trained the same way Section 15C.2 describes (gradient descent on binary
# cross-entropy). Implemented from scratch rather than via a library both to
# keep it small and inspectable, and because a dot-product-and-sigmoid
# scoring loop is exactly the kind of numeric kernel Phase 9 ports to C++
# below.

try:
    from runtime.routing import _native as _native_router  # type: ignore[attr-defined]
except ImportError:
    # Phase 9's C++ extension is optional at runtime (Section 15A.1's
    # pattern, same as Redis/MCP/distributed workers elsewhere in this
    # project): not having built it just means `backend="cpp"` isn't
    # available, not that the pure-Python path -- this module's own
    # reference implementation -- stops working.
    _native_router = None

Backend = Literal["python", "cpp"]


def native_backend_available() -> bool:
    return _native_router is not None


def _require_native() -> None:
    if _native_router is None:
        raise RuntimeError(
            "backend='cpp' requires the compiled runtime.routing._native "
            "extension -- build it with `python setup.py build_ext --inplace`"
        )

FEATURE_KEYS = [
    "static_reliability",
    "static_latency_seconds",
    "cost_per_call",
    "observed_reliability",
    "observed_latency_seconds",
]


def raw_feature_vector(features: dict) -> list[float]:
    """Pulls a fixed-order numeric vector out of a feature snapshot dict
    (`runtime.routing.heuristic.build_feature_vector`). `observed_*` fields
    are None at cold start (Section 15C.3) -- imputed with the corresponding
    static prior, the same assumption the heuristic itself makes when it has
    no history to fall back on.
    """
    observed_reliability = features.get("observed_reliability")
    observed_latency = features.get("observed_latency_seconds")
    return [
        features["static_reliability"],
        features["static_latency_seconds"],
        features["cost_per_call"],
        observed_reliability if observed_reliability is not None else features["static_reliability"],
        observed_latency if observed_latency is not None else features["static_latency_seconds"],
    ]


def _standardize(vector: list[float], means: list[float], stds: list[float]) -> list[float]:
    return [(x - m) / s if s > 1e-9 else 0.0 for x, m, s in zip(vector, means, stds)]


def _clip(z: float, bound: float = 35.0) -> float:
    # Keeps math.exp from overflowing on a large |z| during early training
    # iterations, before the weights have settled down.
    return max(-bound, min(bound, z))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-_clip(z)))


@dataclass(frozen=True)
class NeuralScorer:
    """A trained router model (Section 15G.2's `router_models`): weights are
    plain floats, not an opaque serialized object, so the model can be
    persisted as JSON and inspected directly.
    """

    model_version: str
    feature_version: str
    training_dataset_version: str
    weights: list[float]
    bias: float
    feature_means: list[float] = field(default_factory=list)
    feature_stds: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "training_dataset_version": self.training_dataset_version,
            "weights": self.weights,
            "bias": self.bias,
            "feature_means": self.feature_means,
            "feature_stds": self.feature_stds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NeuralScorer":
        return cls(**data)


def predict_proba(scorer: NeuralScorer, features: dict) -> float:
    """P(success) for one candidate under the trained model. Never raises on
    a malformed feature dict missing a key the model wasn't trained to
    expect -- Section 15A.1's "on... invalid candidate, use the deterministic
    baseline" means a bad input here should fall back, not crash dispatch.
    """
    vector = _standardize(raw_feature_vector(features), scorer.feature_means, scorer.feature_stds)
    z = scorer.bias + sum(w * x for w, x in zip(scorer.weights, vector))
    return _sigmoid(z)


def predict_proba_batch(
    scorer: NeuralScorer, feature_dicts: list[dict], backend: Backend = "python"
) -> list[float]:
    """P(success) for every candidate in one call, in the same order as
    `feature_dicts` -- the natural integration point for Phase 9's C++
    kernel (`runtime.routing._native.predict_proba_batch`), which amortizes
    one Python<->native crossing over a whole batch instead of paying it once
    per candidate the way a loop of `predict_proba` calls would. Dispatch
    (`runtime.routing.dispatch._neural_scores_for`) always has a small batch
    of this shape on hand already -- every candidate tool for one task's
    capability -- so this is a drop-in replacement for what used to be a
    dict comprehension of `predict_proba` calls there, not a new code path.
    """
    if backend == "cpp":
        _require_native()
        vectors = [raw_feature_vector(f) for f in feature_dicts]
        return _native_router.predict_proba_batch(
            scorer.weights, scorer.bias, scorer.feature_means, scorer.feature_stds, vectors
        )
    return [predict_proba(scorer, f) for f in feature_dicts]


def train_logistic_regression(
    examples: list[tuple[dict, bool]],
    *,
    model_version: str,
    feature_version: str,
    training_dataset_version: str,
    epochs: int = 300,
    learning_rate: float = 0.5,
    l2: float = 0.001,
    backend: Backend = "python",
) -> NeuralScorer:
    """Batch gradient descent on binary cross-entropy (Section 15C.2:
    "initially train a binary success predictor with cross-entropy").
    `examples` is (feature_snapshot, success_label) pairs -- typically
    `runtime.training.datasets.selected_candidate_features` output paired
    with a dataset row's `success`, restricted to the "train" split.

    `backend="cpp"` (Phase 9) runs the exact same algorithm --
    standardization, then `epochs` full passes of batch gradient descent --
    through `runtime.routing._native.train_logistic_regression` instead of
    the loop below, which stays the project's reference implementation (what
    `tests/test_neural_cpp.py` checks the native path against, and what runs
    whenever the extension hasn't been built -- see `native_backend_
    available`). Training is the one place in this module where that
    compiled path is honestly worth having: it is O(epochs * n * n_features)
    of repeated arithmetic, the only workload here that grows enough for an
    interpreter's per-operation overhead to show up in wall-clock time (see
    `benchmarks/benchmark_cpp_router.py` for the measured difference).
    """
    if not examples:
        raise ValueError("cannot train on an empty example set")

    vectors = [raw_feature_vector(f) for f, _ in examples]
    labels = [1.0 if label else 0.0 for _, label in examples]

    if backend == "cpp":
        _require_native()
        fitted = _native_router.train_logistic_regression(vectors, labels, epochs, learning_rate, l2)
        weights, bias = fitted["weights"], fitted["bias"]
        means, stds = fitted["feature_means"], fitted["feature_stds"]
    else:
        n_features = len(FEATURE_KEYS)
        n = len(vectors)

        means = [sum(v[i] for v in vectors) / n for i in range(n_features)]
        stds = [
            math.sqrt(sum((v[i] - means[i]) ** 2 for v in vectors) / n) or 1.0
            for i in range(n_features)
        ]
        standardized = [_standardize(v, means, stds) for v in vectors]

        weights = [0.0] * n_features
        bias = 0.0
        for _ in range(epochs):
            grad_w = [0.0] * n_features
            grad_b = 0.0
            for x, y in zip(standardized, labels):
                pred = _sigmoid(bias + sum(w * xi for w, xi in zip(weights, x)))
                error = pred - y
                for i in range(n_features):
                    grad_w[i] += error * x[i]
                grad_b += error
            weights = [w - learning_rate * (grad_w[i] / n + l2 * w) for i, w in enumerate(weights)]
            bias -= learning_rate * (grad_b / n)

    return NeuralScorer(
        model_version=model_version,
        feature_version=feature_version,
        training_dataset_version=training_dataset_version,
        weights=weights,
        bias=bias,
        feature_means=means,
        feature_stds=stds,
    )
