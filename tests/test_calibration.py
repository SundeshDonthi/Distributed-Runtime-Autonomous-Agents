from __future__ import annotations

import pytest

from runtime.training.calibrate import (
    ECE_THRESHOLD,
    MIN_CALIBRATION_SAMPLES,
    compute_calibration,
    is_calibrated,
)


def test_perfectly_calibrated_predictions_have_near_zero_ece():
    # 10 predictions at 0.9, 9 of which succeed -- predicted matches actual.
    predictions = [0.9] * 10
    labels = [True] * 9 + [False]
    calibration = compute_calibration(predictions, labels, n_buckets=10)
    assert calibration["ece"] < 0.05


def test_badly_miscalibrated_predictions_have_high_ece():
    # Predicts near-certain success but almost never succeeds.
    predictions = [0.95] * 20
    labels = [False] * 18 + [True] * 2
    calibration = compute_calibration(predictions, labels, n_buckets=10)
    assert calibration["ece"] > 0.5


def test_empty_predictions_returns_none_fields():
    calibration = compute_calibration([], [])
    assert calibration["n"] == 0
    assert calibration["ece"] is None


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        compute_calibration([0.5, 0.6], [True])


def test_is_calibrated_requires_minimum_sample_size():
    predictions = [0.9] * (MIN_CALIBRATION_SAMPLES - 1)
    labels = [True] * len(predictions)
    calibration = compute_calibration(predictions, labels)
    assert is_calibrated(calibration) is False  # too few samples, regardless of ECE


def test_is_calibrated_true_for_enough_well_calibrated_samples():
    predictions = [0.8] * MIN_CALIBRATION_SAMPLES
    labels = [True] * int(MIN_CALIBRATION_SAMPLES * 0.8) + [False] * (
        MIN_CALIBRATION_SAMPLES - int(MIN_CALIBRATION_SAMPLES * 0.8)
    )
    calibration = compute_calibration(predictions, labels)
    assert calibration["ece"] <= ECE_THRESHOLD
    assert is_calibrated(calibration) is True
