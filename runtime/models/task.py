from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class Task(BaseModel):
    id: str
    workflow_id: str
    description: str
    dependencies: list[str] = []
    required_capabilities: list[str] = []
    status: TaskStatus = TaskStatus.PENDING
    input: dict | None = None
    output: dict | None = None
    error: str | None = None
    retry_count: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
