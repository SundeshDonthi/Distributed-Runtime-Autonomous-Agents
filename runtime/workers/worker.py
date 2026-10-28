from __future__ import annotations

import asyncio

from runtime.models.task import Task, TaskStatus
from runtime.routing.heuristic import select_tool
from runtime.state.orm import utcnow
from runtime.tools.registry import UnknownCapabilityError, UnknownToolError, get_tool


class Worker:
    """A single, stateless worker (Section 5.5): receive a task, run its tool,
    persist the result. Phase 1 ran one worker sequentially; Phase 2 lets the
    scheduler dispatch many of these concurrently (tool calls are offloaded to
    a thread so a blocking tool doesn't stall the event loop). Phase 6 adds
    distribution across processes/machines.

    A task addresses work one of two ways (`task.input`):
    - `{"tool": "<name>", "args": {...}}` -- pin an exact tool, bypassing
      routing. What the Phase 1/2 planner and tests use.
    - `{"capability": "<name>", "args": {...}}` -- let the router (currently
      the Section 15 heuristic; a neural scorer plugs in later without
      changing this call site) pick which tool satisfies it.
    """

    def __init__(self, worker_id: str = "worker_1") -> None:
        self.worker_id = worker_id

    async def execute_async(self, task: Task) -> Task:
        task.status = TaskStatus.RUNNING
        task.started_at = utcnow()

        tool_name = (task.input or {}).get("tool")
        capability = (task.input or {}).get("capability")
        args = (task.input or {}).get("args", {})
        try:
            if tool_name is None:
                if capability is None:
                    raise ValueError("task.input must specify 'tool' or 'capability'")
                tool_name = select_tool(capability).name

            tool = get_tool(tool_name)
            task.output = {**await asyncio.to_thread(tool, args), "_tool_used": tool_name}
            task.status = TaskStatus.COMPLETED
        except (UnknownToolError, UnknownCapabilityError) as exc:
            task.status = TaskStatus.FAILED
            task.error = f"routing failed: {exc}"
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
