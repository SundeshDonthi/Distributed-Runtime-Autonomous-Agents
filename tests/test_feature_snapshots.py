from __future__ import annotations

from runtime.models.task import Task
from runtime.routing.dispatch import resolve_task_tool
from runtime.routing.heuristic import FEATURE_VERSION, build_feature_vector, score_candidate
from runtime.state import repository
from runtime.tools.registry import get_tool_metadata


def test_candidate_features_are_persisted_with_the_decision(db_session):
    workflow_id = "wf1"
    repository.create_workflow(db_session, workflow_id, "feature snapshot test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"capability": "lookup", "args": {"query": "x"}},
    )
    repository.save_tasks(db_session, [task])

    decision_id = resolve_task_tool(db_session, task)
    decision = repository.get_routing_decision(db_session, decision_id)

    assert len(decision["candidates"]) == 2
    for candidate in decision["candidates"]:
        assert candidate["feature_version"] == FEATURE_VERSION
        features = candidate["features"]
        assert features["feature_version"] == FEATURE_VERSION
        assert "static_reliability" in features
        assert "static_latency_seconds" in features
        assert "cost_per_call" in features
        # Cold start: no tool_calls history yet, so observed_* fields reflect that.
        assert features["observed_sample_size"] == 0
        assert features["observed_reliability"] is None


def test_persisted_features_reproduce_the_persisted_score(db_session):
    # The whole point of snapshotting features is that recomputing the score
    # from them later gives back exactly what was logged -- if these ever
    # diverged, a training run would be learning from numbers that don't
    # match what the router actually used to decide.
    workflow_id = "wf2"
    repository.create_workflow(db_session, workflow_id, "fidelity test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"capability": "lookup", "args": {"query": "x"}},
    )
    repository.save_tasks(db_session, [task])

    decision_id = resolve_task_tool(db_session, task)
    decision = repository.get_routing_decision(db_session, decision_id)

    for candidate in decision["candidates"]:
        tool = get_tool_metadata(candidate["tool_name"])
        features = candidate["features"]
        # Reconstruct an ObservedStats-shaped score input purely from the
        # snapshot, with no new DB queries -- proving the snapshot alone is
        # sufficient to reproduce the decision.
        from runtime.routing.history import ObservedStats

        observed = (
            None
            if features["observed_sample_size"] == 0
            else ObservedStats(
                success_rate=features["observed_reliability"],
                avg_latency_seconds=features["observed_latency_seconds"],
                sample_size=features["observed_sample_size"],
            )
        )
        recomputed = score_candidate(tool, observed=observed)
        assert recomputed == candidate["score"]


def test_build_feature_vector_reflects_observed_stats_when_present():
    from runtime.routing.history import ObservedStats

    tool = get_tool_metadata("lookup_fast")
    observed = ObservedStats(success_rate=0.5, avg_latency_seconds=0.2, sample_size=10)

    features = build_feature_vector(tool, observed)

    assert features["observed_reliability"] == 0.5
    assert features["observed_latency_seconds"] == 0.2
    assert features["observed_sample_size"] == 10
    # Static priors are still recorded alongside, for auditability.
    assert features["static_reliability"] == tool.reliability
