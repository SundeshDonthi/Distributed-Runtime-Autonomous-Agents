"""Standalone entry point used by tests/test_chaos_recovery.py to run a
workflow in a genuinely separate OS process, so it can be killed (SIGKILL,
no cleanup chance) mid-execution to test real crash recovery -- Phase 4's own
definition of done: "killing workers does not erase completed tasks."

Usage: python -m scripts.chaos_run_workflow <workflow_id> [lease_seconds]
"""
from __future__ import annotations

import sys

from runtime.scheduler.scheduler import run_workflow
from runtime.state.db import get_session


def main() -> None:
    workflow_id = sys.argv[1]
    lease_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    with get_session() as session:
        run_workflow(session, workflow_id, lease_seconds=lease_seconds)


if __name__ == "__main__":
    main()
