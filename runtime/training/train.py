from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from runtime.routing.heuristic import FEATURE_VERSION
from runtime.routing.neural import NeuralScorer, train_logistic_regression
from runtime.training.datasets import export_training_rows, selected_candidate_features

MIN_TRAINING_EXAMPLES = 20


class InsufficientTrainingDataError(Exception):
    pass


def _dataset_version(decision_ids: list[str]) -> str:
    """A short, deterministic fingerprint of exactly which decisions this
    model was trained on (Section 15D.2: "version... training dataset
    version") -- lets a later audit confirm two models claiming the same
    training_dataset_version really were trained on identical data.
    """
    digest = hashlib.sha256(",".join(sorted(decision_ids)).encode()).hexdigest()
    return digest[:16]


def train_router_model(
    session: Session,
    *,
    capability: str = "lookup",
    seed: int = 0,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    model_version: str | None = None,
    epochs: int = 300,
    learning_rate: float = 0.5,
) -> tuple[NeuralScorer, dict]:
    """Trains on the "train" split only (Section 15D.2's leakage prevention --
    evaluation against val/test happens separately in `evaluate.py` so
    training and evaluation never accidentally share a code path that could
    leak held-out data into the fit). Returns the trained scorer plus basic
    stats about what it was trained on, for the record.
    """
    rows = export_training_rows(session, seed=seed, train_frac=train_frac, val_frac=val_frac)
    train_rows = [r for r in rows if r["split"] == "train" and r["capability"] == capability]

    examples = []
    for row in train_rows:
        features = selected_candidate_features(row)
        if features is not None:
            examples.append((features, row["success"]))

    if len(examples) < MIN_TRAINING_EXAMPLES:
        raise InsufficientTrainingDataError(
            f"only {len(examples)} labeled training examples for capability "
            f"{capability!r}; need at least {MIN_TRAINING_EXAMPLES}"
        )

    dataset_version = _dataset_version([r["decision_id"] for r in train_rows])
    model_version = model_version or f"neural-v1-{dataset_version[:8]}"

    scorer = train_logistic_regression(
        examples,
        model_version=model_version,
        feature_version=FEATURE_VERSION,
        training_dataset_version=dataset_version,
        epochs=epochs,
        learning_rate=learning_rate,
    )

    n_positive = sum(1 for _, label in examples if label)
    stats = {
        "n_examples": len(examples),
        "n_positive": n_positive,
        "n_negative": len(examples) - n_positive,
        "dataset_version": dataset_version,
    }
    return scorer, stats
