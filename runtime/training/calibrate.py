from __future__ import annotations

MIN_CALIBRATION_SAMPLES = 20
# Deliberately generous given how small this project's held-out sets are
# (tens, not thousands, of examples) -- a strict threshold would just always
# fail and the "is_calibrated" flag would never mean anything.
ECE_THRESHOLD = 0.2


def compute_calibration(predictions: list[float], labels: list[bool], n_buckets: int = 10) -> dict:
    """Expected Calibration Error (ECE) and Brier score over bucketed
    predictions (Section 15B.4: "Confidence is a calibrated estimate only
    after calibration has been measured; otherwise label it as a model
    score."). A model whose predicted probabilities don't line up with
    observed outcome frequencies isn't lying about *which* candidate is
    better, necessarily, but its number can't be trusted as a probability.
    """
    if len(predictions) != len(labels):
        raise ValueError("predictions and labels must be the same length")
    n = len(predictions)
    if n == 0:
        return {"n": 0, "ece": None, "brier_score": None, "buckets": []}

    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_buckets)]
    for p, y in zip(predictions, labels):
        idx = min(int(p * n_buckets), n_buckets - 1)
        buckets[idx].append((p, y))

    ece = 0.0
    bucket_stats = []
    for b in buckets:
        if not b:
            bucket_stats.append(None)
            continue
        avg_predicted = sum(p for p, _ in b) / len(b)
        avg_actual = sum(1.0 if y else 0.0 for _, y in b) / len(b)
        ece += (len(b) / n) * abs(avg_predicted - avg_actual)
        bucket_stats.append(
            {"count": len(b), "avg_predicted": avg_predicted, "avg_actual": avg_actual}
        )

    brier_score = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(predictions, labels)) / n

    return {"n": n, "ece": ece, "brier_score": brier_score, "buckets": bucket_stats}


def is_calibrated(calibration: dict) -> bool:
    """Whether a model's predicted probabilities may be reported as
    "confidence" (Section 15B.4) rather than just a model score. Requires
    both enough held-out samples to trust the measurement and a low enough
    ECE -- either one alone isn't sufficient.
    """
    if calibration["n"] < MIN_CALIBRATION_SAMPLES or calibration["ece"] is None:
        return False
    return calibration["ece"] <= ECE_THRESHOLD
