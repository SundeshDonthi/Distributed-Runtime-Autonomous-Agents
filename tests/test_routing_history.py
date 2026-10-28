from __future__ import annotations

from runtime.models.task import Task
from runtime.routing.history import get_observed_stats, get_observed_stats_for_capability
from runtime.state import repository
from runtime.tools.registry import tools_for_capability


def _record_call(session, task_id: str, tool_name: str, status: str, latency_ms: float) -> None:
    repository.record_tool_call(
        session=session,
        task_id=task_id,
        tool_name=tool_name,
        input={},
        output={},
        latency_ms=latency_ms,
        status=status,
        error=None if status == "COMPLETED" else "boom",
    )


def _make_task_row(session, task_id: str) -> None:
    repository.create_workflow(session, f"wf_{task_id}", "history test")
    repository.save_tasks(
        session, [Task(id=task_id, workflow_id=f"wf_{task_id}", description="t")]
    )


def test_cold_start_returns_none(db_session):
    assert get_observed_stats(db_session, "lookup_fast") is None


def test_observed_stats_reflect_recorded_outcomes(db_session):
    _make_task_row(db_session, "t1")
    _record_call(db_session, "t1", "lookup_fast", "COMPLETED", 50.0)
    _record_call(db_session, "t1", "lookup_fast", "COMPLETED", 60.0)
    _record_call(db_session, "t1", "lookup_fast", "FAILED", 40.0)

    stats = get_observed_stats(db_session, "lookup_fast")

    assert stats is not None
    assert stats.sample_size == 3
    assert stats.success_rate == 2 / 3
    assert stats.avg_latency_seconds == (50.0 + 60.0 + 40.0) / 3 / 1000.0


def test_window_limits_how_far_back_history_is_considered(db_session):
    _make_task_row(db_session, "t2")
    for _ in range(5):
        _record_call(db_session, "t2", "lookup_fast", "FAILED", 50.0)
    for _ in range(2):
        _record_call(db_session, "t2", "lookup_fast", "COMPLETED", 50.0)

    stats = get_observed_stats(db_session, "lookup_fast", window=2)

    assert stats.sample_size == 2
    # most recent 2 calls were the successes recorded last
    assert stats.success_rate == 1.0


def test_stats_for_capability_only_includes_tools_with_history(db_session):
    _make_task_row(db_session, "t3")
    _record_call(db_session, "t3", "lookup_fast", "COMPLETED", 50.0)

    stats = get_observed_stats_for_capability(db_session, tools_for_capability("lookup"))

    assert set(stats) == {"lookup_fast"}
