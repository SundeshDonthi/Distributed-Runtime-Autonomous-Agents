from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from runtime.models.task import Task


class WorkflowStatus(str, Enum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Workflow(BaseModel):
    id: str
    objective: str
    status: WorkflowStatus = WorkflowStatus.PLANNING
    # Bumped each time the replanner mutates the graph (Section 15G.1's
    # "plan_version") -- the doc's own name for exactly this.
    plan_version: int = 1
    created_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowWithTasks(Workflow):
    tasks: list[Task] = []
