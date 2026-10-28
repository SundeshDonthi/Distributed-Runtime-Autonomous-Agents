from __future__ import annotations

import asyncio

import pytest

import runtime.tools.registry as registry
from runtime.models.task import Task, TaskStatus
from runtime.workers.worker import Worker


@pytest.fixture()
def force_tool_success(monkeypatch):
    # lookup_fast/lookup_careful simulate real-world unreliability via
    # random.random() -- force it low so a capability-routing test isn't
    # flaky based on which tool the heuristic happens to pick.
    monkeypatch.setattr(registry.random, "random", lambda: 0.0)


@pytest.fixture()
def force_tool_failure(monkeypatch):
    monkeypatch.setattr(registry.random, "random", lambda: 1.0)


def _capability_task(capability: str) -> Task:
    return Task(
        id="t1",
        workflow_id="wf1",
        description="capability-routed task",
        input={"capability": capability, "args": {"query": "gpu pricing"}},
    )


def test_capability_task_is_routed_to_heuristic_winner(force_tool_success):
    task = _capability_task("lookup")
    result = asyncio.run(Worker().execute_async(task))

    assert result.status == TaskStatus.COMPLETED
    # Default weights favor lookup_fast (see test_routing_heuristic.py) --
    # this proves the worker actually consults the router rather than, say,
    # picking an arbitrary or alphabetically-first candidate.
    assert result.output["_tool_used"] == "lookup_fast"


def test_capability_task_surfaces_simulated_tool_failure(force_tool_failure):
    task = _capability_task("lookup")
    result = asyncio.run(Worker().execute_async(task))

    assert result.status == TaskStatus.FAILED
    assert "simulated failure" in result.error


def test_unknown_capability_fails_cleanly():
    task = _capability_task("does_not_exist")
    result = asyncio.run(Worker().execute_async(task))

    assert result.status == TaskStatus.FAILED
    assert "routing failed" in result.error


def test_explicit_tool_name_bypasses_routing(force_tool_success):
    task = Task(
        id="t2",
        workflow_id="wf1",
        description="explicit tool",
        input={"tool": "lookup_careful", "args": {"query": "x"}},
    )
    result = asyncio.run(Worker().execute_async(task))

    assert result.status == TaskStatus.COMPLETED
    assert result.output["_tool_used"] == "lookup_careful"
