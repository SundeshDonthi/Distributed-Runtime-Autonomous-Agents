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
    error_category: str | None = None  # "transient" | "permanent" | "needs_replan"
    retry_count: int = 0
    max_retries: int = 3
    retry_after: datetime | None = None

    # Dynamic replanning (Section 5.10/15J Phase 5): how many times this
    # specific task has already triggered a graph mutation, bounded by
    # `max_replans` so a prerequisite that never resolves can't loop forever.
    replan_count: int = 0
    max_replans: int = 2
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Leasing/fencing (Section 12/13/15F.4) -- see TaskRow for the rationale.
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    fencing_token: int = 0

    @property
    def idempotency_key(self) -> str:
        """Stable per-task key (workflow_id:task_id), not attempt-based --
        Section 15F.3 explicitly warns an attempt-based key can't dedupe
        retries of the same logical operation.
        """
        return f"{self.workflow_id}:{self.id}"
