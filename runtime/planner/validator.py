from __future__ import annotations

from runtime.models.task import Task


class PlanValidationError(ValueError):
    pass


def validate_plan(tasks: list[Task]) -> None:
    """Enforce the invariants Section 5.2 requires before a plan is persisted:
    dependencies must exist and the graph must be acyclic.
    """
    ids = {t.id for t in tasks}
    if len(ids) != len(tasks):
        raise PlanValidationError("duplicate task ids in plan")

    for task in tasks:
        for dep in task.dependencies:
            if dep not in ids:
                raise PlanValidationError(
                    f"task {task.id!r} depends on unknown task {dep!r}"
                )

    _check_acyclic(tasks)


def _check_acyclic(tasks: list[Task]) -> None:
    deps = {t.id: set(t.dependencies) for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {t.id: WHITE for t in tasks}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for dep in deps[node]:
            if color[dep] == GRAY:
                cycle = " -> ".join(stack[stack.index(dep):] + [dep])
                raise PlanValidationError(f"dependency cycle detected: {cycle}")
            if color[dep] == WHITE:
                visit(dep, stack)
        stack.pop()
        color[node] = BLACK

    for task_id in deps:
        if color[task_id] == WHITE:
            visit(task_id, [])
