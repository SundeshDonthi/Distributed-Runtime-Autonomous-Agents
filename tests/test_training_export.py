from __future__ import annotations

import random

from runtime.training.datasets import (
    export_training_rows,
    run_cold_start_benchmark,
    selected_candidate_features,
)


def test_export_tags_every_row_with_a_split(db_session):
    run_cold_start_benchmark(db_session, 10, explore_probability=0.5, rng=random.Random(0))

    rows = export_training_rows(db_session)

    assert len(rows) == 10
    assert all(row["split"] in {"train", "val", "test"} for row in rows)


def test_a_single_workflows_decisions_never_split_across_sets(db_session):
    # Section 15D.2: "partition train/validation/test by workflow... not
    # individual attempts from the same workflow." All 20 tasks here belong
    # to the SAME workflow, so they must all land in the same split.
    workflow_id = run_cold_start_benchmark(
        db_session, 20, explore_probability=0.5, rng=random.Random(0)
    )

    rows = export_training_rows(db_session, workflow_id=workflow_id)

    assert len(rows) == 20
    assert len({row["split"] for row in rows}) == 1


def test_export_is_reproducible_given_the_same_seed(db_session):
    run_cold_start_benchmark(db_session, 10, explore_probability=0.5, rng=random.Random(0))

    first = export_training_rows(db_session, seed=42)
    second = export_training_rows(db_session, seed=42)

    assert {r["decision_id"]: r["split"] for r in first} == {
        r["decision_id"]: r["split"] for r in second
    }


def test_selected_candidate_features_matches_the_actually_chosen_tool(db_session):
    run_cold_start_benchmark(db_session, 10, explore_probability=0.5, rng=random.Random(0))
    rows = export_training_rows(db_session)

    for row in rows:
        features = selected_candidate_features(row)
        assert features is not None
        assert features["feature_version"]
        selected = [c for c in row["candidates"] if c["tool_name"] == row["selected_tool"]]
        assert features == selected[0]["features"]
