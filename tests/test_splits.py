from __future__ import annotations

import uuid

import pytest

from runtime.training.splits import assign_split


def test_same_inputs_always_produce_the_same_split():
    workflow_id = f"wf_{uuid.uuid4().hex}"
    first = assign_split(workflow_id, seed=0)
    second = assign_split(workflow_id, seed=0)
    assert first == second


def test_different_seeds_can_produce_different_splits():
    workflow_id = f"wf_{uuid.uuid4().hex}"
    results = {assign_split(workflow_id, seed=s) for s in range(50)}
    # Not a strict requirement that they differ, but with 50 distinct seeds
    # on one workflow id, seeing only one split value would indicate the
    # seed isn't actually affecting the hash.
    assert len(results) > 1


def test_splits_roughly_match_requested_proportions():
    counts = {"train": 0, "val": 0, "test": 0}
    n = 2000
    for i in range(n):
        split = assign_split(f"wf_{i}", seed=0, train_frac=0.7, val_frac=0.15)
        counts[split] += 1

    assert 0.65 < counts["train"] / n < 0.75
    assert 0.10 < counts["val"] / n < 0.20
    assert 0.10 < counts["test"] / n < 0.20


def test_invalid_fractions_are_rejected():
    with pytest.raises(ValueError):
        assign_split("wf", train_frac=0.8, val_frac=0.3)  # sums to > 1
    with pytest.raises(ValueError):
        assign_split("wf", train_frac=-0.1)
    with pytest.raises(ValueError):
        assign_split("wf", val_frac=1.5)
