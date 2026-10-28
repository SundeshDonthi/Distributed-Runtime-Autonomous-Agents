from __future__ import annotations

import random

from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.routing.heuristic import DEFAULT_WEIGHTS, RouterWeights, rank_candidates_with_details
from runtime.routing.history import get_observed_stats_for_capability
from runtime.state import repository
from runtime.tools.registry import tools_for_capability

__all__ = ["resolve_task_tool", "record_outcome"]


def resolve_task_tool(
    session: Session,
    task: Task,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    explore_probability: float = 0.0,
    rng: random.Random | None = None,
) -> str | None:
    """Routing step of dispatch (Section 15A.1: the router "ranks eligible
    tool + strategy pairs for an already-READY task" -- a step the scheduler
    owns, not the worker, since it needs the DB session for observed-stats
    history and decision logging; workers stay stateless, Section 5.5).

    A task that already names an explicit `tool` skips routing entirely and
    this returns None. Otherwise resolves `task.input["capability"]` to a
    concrete tool, mutates `task.input["tool"]` in place so the worker can
    execute it unchanged, persists the decision + full candidate set, and
    returns the decision id so the eventual outcome can be attached to it.

    `explore_probability` > 0 occasionally picks a uniformly random candidate
    instead of the heuristic's top-ranked one (epsilon-greedy exploration).
    With it at the default 0, a heuristic that always wins would mean the
    runtime never logs outcomes for its losing candidates -- useful for live
    traffic, but it would make for a biased training set (Section 15D.2).
    Dataset generation (Section 15C.3's "cold start") should set this above
    zero; live routing should generally leave it at 0.
    """
    task_input = task.input or {}
    if task_input.get("tool") is not None:
        return None

    capability = task_input.get("capability")
    if capability is None:
        return None  # malformed input; the worker will raise a clear error

    candidates = tools_for_capability(capability)
    observed = get_observed_stats_for_capability(session, candidates)
    ranked = rank_candidates_with_details(capability, weights, observed)

    was_exploration = False
    selected_tool = ranked[0][0]
    if explore_probability > 0 and len(ranked) > 1:
        rng = rng or random.Random()
        if rng.random() < explore_probability:
            # `was_exploration` records that the random mechanism fired, not
            # whether it happened to land on the same tool the heuristic
            # would have picked anyway -- that sampling-process distinction
            # is what later lets an offline evaluation correctly re-weight
            # exploratory samples (propensity-style) instead of treating
            # every logged decision as equally representative.
            selected_tool = rng.choice(ranked)[0]
            was_exploration = True

    decision_id = repository.create_routing_decision(
        session=session,
        task_id=task.id,
        workflow_id=task.workflow_id,
        capability=capability,
        ranked_candidates=[(tool.name, score, features) for tool, score, features in ranked],
        selected_tool=selected_tool.name,
        weights={
            "capability_match": weights.capability_match,
            "reliability": weights.reliability,
            "latency": weights.latency,
            "cost": weights.cost,
        },
        was_exploration=was_exploration,
    )

    task.input = {**task_input, "tool": selected_tool.name}
    return decision_id


def record_outcome(session: Session, task: Task, decision_id: str) -> None:
    """Persists what actually happened for a routed task: a `tool_calls` row
    (the raw historical record future routing decisions learn from) and a
    `routing_outcomes` row linked back to the decision it resulted from.
    Call only after the task has actually executed.
    """
    latency_ms = 0.0
    if task.started_at is not None and task.completed_at is not None:
        latency_ms = (task.completed_at - task.started_at).total_seconds() * 1000.0

    tool_name = (task.input or {}).get("tool", "")
    success = task.status == TaskStatus.COMPLETED

    repository.record_tool_call(
        session=session,
        task_id=task.id,
        tool_name=tool_name,
        input=(task.input or {}).get("args"),
        output=task.output,
        latency_ms=latency_ms,
        status=task.status.value,
        error=task.error,
    )
    repository.create_routing_outcome(
        session=session, decision_id=decision_id, success=success, latency_ms=latency_ms, error=task.error
    )
