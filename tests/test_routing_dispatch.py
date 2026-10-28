from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models.task import Task, TaskStatus
from runtime.routing.dispatch import record_outcome, resolve_task_tool
from runtime.state import repository


def _make_task(session, task_id: str, input: dict) -> Task:
    workflow_id = f"wf_{task_id}"
    repository.create_workflow(session, workflow_id, "dispatch test")
    task = Task(id=task_id, workflow_id=workflow_id, description="t", input=input)
    repository.save_tasks(session, [task])
    return task


def test_explicit_tool_skips_routing_entirely(db_session):
    task = _make_task(db_session, "t1", {"tool": "echo", "args": {}})

    decision_id = resolve_task_tool(db_session, task)

    assert decision_id is None
    assert task.input == {"tool": "echo", "args": {}}  # untouched


def test_capability_task_gets_resolved_and_logged(db_session):
    task = _make_task(db_session, "t2", {"capability": "lookup", "args": {"query": "x"}})

    decision_id = resolve_task_tool(db_session, task)

    assert decision_id is not None
    # Resolution mutates task.input in place so the worker can execute it
    # unchanged, while keeping the original capability for auditability.
    assert task.input["capability"] == "lookup"
    assert task.input["tool"] in {"lookup_fast", "lookup_careful"}

    decision = repository.get_routing_decision(db_session, decision_id)
    assert decision is not None
    assert decision["capability"] == "lookup"
    assert {c["tool_name"] for c in decision["candidates"]} == {"lookup_fast", "lookup_careful"}
    assert decision["selected_tool"] == task.input["tool"]
    assert decision["outcome"] is None  # task hasn't run yet


def test_record_outcome_persists_tool_call_and_outcome(db_session):
    task = _make_task(db_session, "t3", {"capability": "lookup", "args": {"query": "x"}})
    decision_id = resolve_task_tool(db_session, task)

    task.status = TaskStatus.COMPLETED
    task.started_at = datetime.now(timezone.utc)
    task.completed_at = task.started_at + timedelta(milliseconds=42)
    task.output = {"result": "x"}

    record_outcome(db_session, task, decision_id)

    dataset = repository.get_routing_dataset(db_session)
    assert len(dataset) == 1
    assert dataset[0]["success"] is True
    assert dataset[0]["latency_ms"] == pytest.approx(42.0)

    calls = repository.get_recent_tool_calls(db_session, task.input["tool"], limit=10)
    assert len(calls) == 1
    assert calls[0].status == "COMPLETED"


def test_router_shifts_choice_after_enough_bad_outcomes(db_session):
    # Default weights pick lookup_fast when both tools are at their static
    # priors (see test_routing_heuristic.py). Simulate lookup_fast actually
    # failing in practice and confirm the router starts avoiding it based on
    # that history -- this is the "prior outcomes" half of tool selection,
    # not just the static formula.
    workflow_id = "wf_shift"
    repository.create_workflow(db_session, workflow_id, "shift test")
    seed_task = Task(id="seed", workflow_id=workflow_id, description="seed")
    repository.save_tasks(db_session, [seed_task])

    for _ in range(10):
        repository.record_tool_call(
            session=db_session,
            task_id="seed",
            tool_name="lookup_fast",
            input={},
            output=None,
            latency_ms=50.0,
            status="FAILED",
            error="simulated failure",
        )

    task = Task(
        id="routed",
        workflow_id=workflow_id,
        description="t",
        input={"capability": "lookup", "args": {}},
    )
    repository.save_tasks(db_session, [task])

    resolve_task_tool(db_session, task)

    assert task.input["tool"] == "lookup_careful"
