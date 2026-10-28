from __future__ import annotations

import asyncio

import pytest

from runtime.models.task import Task, TaskStatus
from runtime.tools.registry import ToolMetadata, register_tool, unregister_tool
from runtime.workers.worker import Worker


def _run(task: Task) -> Task:
    return asyncio.run(Worker().execute_async(task))


def test_valid_args_execute_successfully():
    task = Task(
        id="t1", workflow_id="wf1", description="t", input={"tool": "echo", "args": {"text": "hi"}}
    )
    result = _run(task)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == {"result": "hi"}


def test_missing_resolved_tool_fails_cleanly():
    task = Task(id="t2", workflow_id="wf1", description="t", input={"args": {}})
    result = _run(task)
    assert result.status == TaskStatus.FAILED
    assert "resolved 'tool'" in result.error


def test_unknown_tool_fails_cleanly():
    task = Task(
        id="t3", workflow_id="wf1", description="t", input={"tool": "does_not_exist", "args": {}}
    )
    result = _run(task)
    assert result.status == TaskStatus.FAILED
    assert "unknown tool" in result.error


@pytest.fixture()
def strict_tool():
    # A tool with a genuinely required, typed field, to prove schema
    # validation actually runs rather than only ever seeing the lenient
    # built-in tools, all of which treat their args as optional.
    name = "strict_demo_tool"
    register_tool(
        ToolMetadata(
            name=name,
            capability="demo",
            fn=lambda args: {"seconds": args["seconds"]},
            base_latency_seconds=0.0,
            reliability=1.0,
            cost_per_call=0.0,
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
                "required": ["seconds"],
            },
        )
    )
    yield name
    unregister_tool(name)


def test_schema_validation_rejects_wrong_type(strict_tool):
    task = Task(
        id="t4",
        workflow_id="wf1",
        description="t",
        input={"tool": strict_tool, "args": {"seconds": "not-a-number"}},
    )
    result = _run(task)
    assert result.status == TaskStatus.FAILED
    assert "schema validation failed" in result.error


def test_schema_validation_rejects_missing_required_field(strict_tool):
    task = Task(id="t5", workflow_id="wf1", description="t", input={"tool": strict_tool, "args": {}})
    result = _run(task)
    assert result.status == TaskStatus.FAILED
    assert "schema validation failed" in result.error


def test_schema_validation_allows_valid_args(strict_tool):
    task = Task(
        id="t6",
        workflow_id="wf1",
        description="t",
        input={"tool": strict_tool, "args": {"seconds": 1.5}},
    )
    result = _run(task)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == {"seconds": 1.5}
