from __future__ import annotations

from runtime.models.task import Task, TaskStatus
from runtime.planner.planner import Planner
from runtime.planner.validator import PlanValidationError, validate_plan
from runtime.state import repository

__all__ = ["replan_for_missing_prerequisite"]


def replan_for_missing_prerequisite(
    session, all_tasks: list[Task], blocked_task: Task, planner: Planner
) -> list[Task]:
    """Section 5.10: a task discovered mid-execution that it needs more
    information; mutate the graph to add it. "The planner should only modify
    unfinished portions of the graph so completed work remains reusable" is
    satisfied structurally here: the mutation is purely additive (new task
    ids, one new dependency edge onto the blocked task), so nothing already
    completed is touched.

    `blocked_task` must be the same object present in `all_tasks` (not a
    copy) -- it's mutated in place, and `all_tasks` is used as-is for
    cycle/dependency validation of the *resulting* graph, so the mutation
    needs to already be visible there when that runs.

    On success, returns the newly inserted tasks (already persisted) and
    leaves `blocked_task` as PENDING with the new dependency. On failure
    (replan budget exhausted, the planner produced nothing, or the mutated
    graph would be invalid), leaves `blocked_task` as FAILED and returns [].
    """
    assert blocked_task in all_tasks, "blocked_task must be the same object held in all_tasks"

    if blocked_task.replan_count >= blocked_task.max_replans:
        # Bounded replan loops (Section 15J): stop mutating the graph rather
        # than chase a prerequisite that never resolves.
        blocked_task.status = TaskStatus.FAILED
        blocked_task.error = f"replan budget exhausted: {blocked_task.error}"
        return []

    context = blocked_task.error or "missing prerequisite"
    new_tasks = planner.generate_subplan(
        blocked_task.workflow_id, blocked_task.id, blocked_task.replan_count, context
    )
    if not new_tasks:
        blocked_task.status = TaskStatus.FAILED
        blocked_task.error = f"replanner produced no tasks for: {context}"
        return []

    existing_ids = {t.id for t in all_tasks}
    for new_task in new_tasks:
        if new_task.id in existing_ids:
            blocked_task.status = TaskStatus.FAILED
            blocked_task.error = f"replanner generated a colliding task id: {new_task.id}"
            return []
        if new_task.dependencies:
            blocked_task.status = TaskStatus.FAILED
            blocked_task.error = f"replanner task {new_task.id} must have no dependencies of its own"
            return []

    blocked_task.dependencies = [*blocked_task.dependencies, *(t.id for t in new_tasks)]
    blocked_task.status = TaskStatus.PENDING
    blocked_task.replan_count += 1
    blocked_task.error = None
    blocked_task.error_category = None
    # Deliberately NOT clearing lease_owner/lease_expires_at here: this write
    # goes through `commit_task_if_fencing_valid`, which compares the
    # fencing fields on this very object against the current row -- clearing
    # lease_owner would make that comparison fail against the lease this
    # task was legitimately dispatched under this round, silently dropping
    # the whole mutation. Harmless to leave stale: `find_expired_leases` only
    # ever looks at RUNNING tasks, and the next `acquire_lease` overwrites
    # both fields regardless.

    try:
        validate_plan([*all_tasks, *new_tasks])
    except PlanValidationError as exc:
        # Keep the graph in a known-good state; don't persist a broken
        # mutation just because the attempt to fix it failed.
        blocked_task.status = TaskStatus.FAILED
        blocked_task.error = f"replan produced an invalid graph: {exc}"
        blocked_task.replan_count -= 1
        return []

    repository.save_tasks(session, new_tasks)
    for new_task in new_tasks:
        repository.add_task_dependency(session, blocked_task.id, new_task.id)
    plan_version = repository.bump_plan_version(session, blocked_task.workflow_id)
    repository.record_replan_event(
        session=session,
        workflow_id=blocked_task.workflow_id,
        blocked_task_id=blocked_task.id,
        context=context,
        new_task_ids=[t.id for t in new_tasks],
        plan_version=plan_version,
    )
    return new_tasks
