from __future__ import annotations

import asyncio

import jsonschema

from runtime.models.task import Task, TaskStatus
from runtime.state.orm import utcnow
from runtime.tools.registry import UnknownToolError, get_tool_metadata


class Worker:
    """A single, stateless worker (Section 5.5): receive a task, run its tool,
    persist the result. Phase 1 ran one worker sequentially; Phase 2 lets the
    scheduler dispatch many of these concurrently (tool calls are offloaded to
    a thread so a blocking tool doesn't stall the event loop). Phase 6 adds
    distribution across processes/machines.

    A worker only ever executes an already-resolved `{"tool": "<name>", "args":
    {...}}` task -- it does not route a `{"capability": ...}` task itself.
    Routing (Section 15A.1) happens one layer up, in the scheduler's dispatch
    step (`runtime.routing.dispatch.resolve_task_tool`), because choosing a
    tool from historical outcomes needs the DB session a stateless worker
    deliberately doesn't have. What the worker does own is validating `args`
    against the resolved tool's declared JSON schema before calling it
    (Section 15A.1: "never bypasses... schema validation") -- this applies
    equally to tools registered in-process and ones discovered over MCP.
    """

    def __init__(self, worker_id: str = "worker_1") -> None:
        self.worker_id = worker_id

    async def execute_async(self, task: Task) -> Task:
        task.status = TaskStatus.RUNNING
        task.started_at = utcnow()

        tool_name = (task.input or {}).get("tool")
        args = (task.input or {}).get("args", {})
        try:
            if tool_name is None:
                raise ValueError("task.input must specify a resolved 'tool'")
            metadata = get_tool_metadata(tool_name)
            try:
                jsonschema.validate(instance=args, schema=metadata.input_schema)
            except jsonschema.exceptions.ValidationError as exc:
                raise ValueError(f"schema validation failed: {exc.message}") from exc
            task.output = await asyncio.to_thread(metadata.fn, args)
            task.status = TaskStatus.COMPLETED
        except UnknownToolError as exc:
            task.status = TaskStatus.FAILED
            task.error = f"unknown tool: {exc}"
        except Exception as exc:  # noqa: BLE001 - surfaced via task.error, not raised
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        finally:
            task.completed_at = utcnow()

        return task

    def execute(self, task: Task) -> Task:
        """Sync convenience wrapper over `execute_async` for callers (scripts,
        a REPL) that aren't already inside an event loop.
        """
        return asyncio.run(self.execute_async(task))
