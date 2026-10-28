from __future__ import annotations

import time
from datetime import timedelta

import pytest

from runtime.models.task import Task, TaskStatus
from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.orm import utcnow
from runtime.tools.registry import ToolMetadata, register_tool, unregister_tool


@pytest.fixture()
def flaky_tool():
    """A tool that fails its first `fail_times` calls, then succeeds --
    deterministic stand-in for lookup_fast/careful's randomness, so retry
    behavior can be tested exactly rather than probabilistically.
    """
    name = "flaky_test_tool"
    state = {"calls": 0}

    def fn(args: dict) -> dict:
        state["calls"] += 1
        if state["calls"] <= args.get("fail_times", 0):
            raise RuntimeError("transient failure")
        return {"result": "ok", "attempt": state["calls"]}

    register_tool(
        ToolMetadata(name=name, capability="flaky", fn=fn, base_latency_seconds=0.0, reliability=1.0, cost_per_call=0.0)
    )
    yield name, state
    unregister_tool(name)


def test_transient_failure_is_retried_and_eventually_succeeds(db_session, flaky_tool):
    tool_name, state = flaky_tool
    workflow_id = "wf_retry_success"
    repository.create_workflow(db_session, workflow_id, "retry test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"tool": tool_name, "args": {"fail_times": 2}},
    )
    repository.save_tasks(db_session, [task])

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.COMPLETED
    final = repository.get_tasks(db_session, workflow_id)[0]
    assert final.status == TaskStatus.COMPLETED
    assert final.retry_count == 2
    assert state["calls"] == 3  # 2 failures + 1 success


def test_transient_failure_gives_up_after_max_retries(db_session, flaky_tool):
    tool_name, state = flaky_tool
    workflow_id = "wf_retry_exhausted"
    repository.create_workflow(db_session, workflow_id, "retry exhaustion test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"tool": tool_name, "args": {"fail_times": 99}},
        max_retries=2,
    )
    repository.save_tasks(db_session, [task])

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.FAILED
    final = repository.get_tasks(db_session, workflow_id)[0]
    assert final.status == TaskStatus.FAILED
    assert final.retry_count == 2  # tried the original + 2 retries, then gave up
    assert state["calls"] == 3


def test_simulated_crash_leaves_task_reclaimable(db_session):
    # Simulates a previous process acquiring a lease on this task and then
    # crashing before it could report back -- no attempt was recorded. A
    # fresh scheduler run should detect the stale claim and reclaim it,
    # rather than leaving it stuck RUNNING forever.
    workflow_id = "wf_crash_recovery"
    repository.create_workflow(db_session, workflow_id, "crash recovery test")
    task = Task(
        id="t1",
        workflow_id=workflow_id,
        description="t",
        input={"tool": "echo", "args": {"text": "recovered"}},
        status=TaskStatus.RUNNING,
        lease_owner="dead_worker",
        lease_expires_at=utcnow() - timedelta(seconds=1),
        fencing_token=1,
    )
    repository.save_tasks(db_session, [task])
    # save_tasks doesn't persist lease/status fields (it's meant for a fresh
    # plan) -- write them explicitly to set up the "orphaned mid-flight" state.
    repository.update_task(db_session, task)

    status = run_workflow(db_session, workflow_id)

    assert status == WorkflowStatus.COMPLETED
    final = repository.get_tasks(db_session, workflow_id)[0]
    assert final.status == TaskStatus.COMPLETED
    assert final.output == {"result": "recovered"}
    assert final.retry_count == 1  # the reclaim itself counted as one


def test_heartbeat_prevents_a_slow_task_from_being_wrongly_reclaimed(db_session):
    # Lease shorter than the task's own runtime; the heartbeat must renew it
    # several times during the call for this to complete without the task
    # ever being (wrongly) treated as orphaned.
    workflow_id = "wf_heartbeat"
    repository.create_workflow(db_session, workflow_id, "heartbeat test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t",
        input={"tool": "delay", "args": {"seconds": 0.5}},
    )
    repository.save_tasks(db_session, [task])

    start = time.perf_counter()
    status = run_workflow(db_session, workflow_id, lease_seconds=0.15, heartbeat_interval=0.05)
    elapsed = time.perf_counter() - start

    assert status == WorkflowStatus.COMPLETED
    final = repository.get_tasks(db_session, workflow_id)[0]
    assert final.status == TaskStatus.COMPLETED
    assert final.retry_count == 0  # never reclaimed
    assert elapsed < 1.0  # completed once, not re-run after a wrongful reclaim


def test_task_transitions_write_outbox_events(db_session):
    workflow_id = "wf_outbox"
    repository.create_workflow(db_session, workflow_id, "outbox test")
    task = Task(
        id="t1", workflow_id=workflow_id, description="t", input={"tool": "echo", "args": {"text": "x"}}
    )
    repository.save_tasks(db_session, [task])

    run_workflow(db_session, workflow_id)

    events = repository.get_undelivered_outbox_events(db_session)
    event_types = {(e.aggregate_id, e.event_type) for e in events}
    assert ("t1", "task.completed") in event_types

    repository.mark_outbox_event_delivered(db_session, next(e.id for e in events if e.aggregate_id == "t1"))
    remaining = repository.get_undelivered_outbox_events(db_session)
    assert "t1" not in {e.aggregate_id for e in remaining}


def test_get_outbox_events_for_workflow_does_not_leak_across_workflows(db_session):
    """`OutboxEventRow.aggregate_id` is a task id, not a workflow id --
    `get_outbox_events_for_workflow` (Section 15J Phase 8's `/trace`
    endpoint) has to join through `tasks` to scope correctly, so this proves
    that join doesn't accidentally match a same-named task id from another
    workflow.
    """
    repository.create_workflow(db_session, "wf_outbox_a", "a")
    repository.create_workflow(db_session, "wf_outbox_b", "b")
    repository.save_tasks(
        db_session,
        [
            Task(id="t1", workflow_id="wf_outbox_a", description="t",
                 input={"tool": "echo", "args": {"text": "a"}}),
            Task(id="t1_b", workflow_id="wf_outbox_b", description="t",
                 input={"tool": "echo", "args": {"text": "b"}}),
        ],
    )

    run_workflow(db_session, "wf_outbox_a")
    run_workflow(db_session, "wf_outbox_b")

    events_a = repository.get_outbox_events_for_workflow(db_session, "wf_outbox_a")
    assert {e["task_id"] for e in events_a} == {"t1"}
    events_b = repository.get_outbox_events_for_workflow(db_session, "wf_outbox_b")
    assert {e["task_id"] for e in events_b} == {"t1_b"}
    # Oldest first -- a trace reads top-to-bottom as "what happened, in order."
    assert [e["id"] for e in events_a] == sorted(e["id"] for e in events_a)
