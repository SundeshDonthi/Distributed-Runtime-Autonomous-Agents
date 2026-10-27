from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.state.orm import utcnow
from runtime.tools.registry import UnknownToolError, get_tool


class Worker:
    """A single, stateless worker (Section 5.5): receive a task, run its tool,
    persist the result. Phase 1 runs one worker in-process and sequentially;
    Phase 2 adds concurrency, Phase 6 adds distribution across processes.
    """

    def __init__(self, worker_id: str = "worker_1") -> None:
        self.worker_id = worker_id

    def execute(self, task: Task) -> Task:
        task.status = TaskStatus.RUNNING
        task.started_at = utcnow()

        tool_name = (task.input or {}).get("tool")
        args = (task.input or {}).get("args", {})
        try:
            tool = get_tool(tool_name)
            task.output = tool(args)
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
