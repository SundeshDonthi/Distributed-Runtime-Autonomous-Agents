from __future__ import annotations

import math
from dataclasses import dataclass, field

# Section 15C.1's "compact text encoder + MLP" architecture doesn't have a
# meaningful text signal to encode at this project's current scale -- task
# descriptions are synthetic strings like "benchmark lookup call 3", not
# anything with real semantic content. At that feature richness the
# architecture honestly degenerates to logistic regression: a single linear
# layer + sigmoid, which is literally the zero-hidden-layer case of an MLP,
# trained the same way Section 15C.2 describes (gradient descent on binary
# cross-entropy). Implemented from scratch rather than via a library both to
# keep it small and inspectable, and because a dot-product-and-sigmoid
# scoring loop is exactly the kind of numeric kernel Phase 9 can honestly
# port to C++ later.

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


def train_logistic_regression(
    examples: list[tuple[dict, bool]],
    *,
    model_version: str,
    feature_version: str,
    training_dataset_version: str,
    epochs: int = 300,
    learning_rate: float = 0.5,
    l2: float = 0.001,
) -> NeuralScorer:
    """Batch gradient descent on binary cross-entropy (Section 15C.2:
    "initially train a binary success predictor with cross-entropy").
    `examples` is (feature_snapshot, success_label) pairs -- typically
    `runtime.training.datasets.selected_candidate_features` output paired
    with a dataset row's `success`, restricted to the "train" split.
    """
    if not examples:
        raise ValueError("cannot train on an empty example set")

    vectors = [raw_feature_vector(f) for f, _ in examples]
    labels = [1.0 if label else 0.0 for _, label in examples]
    n_features = len(FEATURE_KEYS)
    n = len(vectors)

    means = [sum(v[i] for v in vectors) / n for i in range(n_features)]
    stds = [
        math.sqrt(sum((v[i] - means[i]) ** 2 for v in vectors) / n) or 1.0 for i in range(n_features)
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
