from __future__ import annotations

import random
from typing import Literal

from sqlalchemy.orm import Session

from runtime.models.task import Task, TaskStatus
from runtime.routing.heuristic import (
    DEFAULT_WEIGHTS,
    FEATURE_VERSION,
    RouterWeights,
    rank_candidates_with_details,
)
from runtime.routing.history import get_observed_stats_for_capability
from runtime.routing.neural import Backend, NeuralScorer, predict_proba_batch
from runtime.state import repository
from runtime.tools.registry import tools_for_capability

__all__ = ["resolve_task_tool", "record_outcome"]

RoutingMode = Literal["heuristic", "shadow", "active"]
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


def _neural_scores_for(
    router_model: NeuralScorer, ranked: list[tuple], neural_backend: Backend = "python"
) -> dict[str, float] | None:
    """Scores every eligible candidate under the neural model, in one
    `predict_proba_batch` call rather than one `predict_proba` call per
    candidate (Phase 9: the natural place to opt into the C++ batched-
    inference kernel via `neural_backend`). Returns None (rather than
    raising) if the model's feature schema doesn't match what's being logged
    today (Section 15D.2: a model trained on a stale feature version
    shouldn't be trusted blindly -- its numbers would silently mean something
    different from what the caller assumes) or if inference itself fails for
    any reason -- Section 15A.1's "on... invalid candidate, use the
    deterministic baseline" extends naturally to "on error, don't propagate
    it into dispatch."
    """
    if router_model.feature_version != FEATURE_VERSION:
        return None
    try:
        tools = [tool.name for tool, _score, _features in ranked]
        feature_dicts = [features for _tool, _score, features in ranked]
        scores = predict_proba_batch(router_model, feature_dicts, backend=neural_backend)
        return dict(zip(tools, scores))
    except Exception:  # noqa: BLE001 - any inference failure just means "no neural scores"
        return None


def resolve_task_tool(
    session: Session,
    task: Task,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    explore_probability: float = 0.0,
    rng: random.Random | None = None,
    router_model: NeuralScorer | None = None,
    routing_mode: RoutingMode = "heuristic",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    neural_backend: Backend = "python",
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

    The heuristic ranking is *always* computed, regardless of `routing_mode`
    -- it's both the Section 15A.1 fallback and, in "heuristic" mode, the
    only thing that ever decides:

    - "heuristic" (default): unaffected by `router_model` even if one is
      passed. The permanent baseline (Section 15A.1).
    - "shadow" (Section 15D.3 stage 2): the heuristic still decides, but if
      `router_model` is given, every candidate's neural score is also
      computed and logged -- collecting evidence of how the model *would*
      have performed, without it influencing anything yet.
    - "active" (Section 15D.3 stage 3/4): the neural model's top-ranked
      candidate is used instead, but only if a model was actually supplied,
      its top pick clears `confidence_threshold`, and that pick is among the
      eligible candidates -- otherwise this falls back to the heuristic's
      choice and records why in `fallback_reason` (Section 15G.2).

    `explore_probability` > 0 occasionally overrides whichever choice was
    made (heuristic or neural) with a uniformly random candidate instead
    (epsilon-greedy exploration) -- this is about data collection, not about
    which policy nominally decided, so it applies the same way regardless of
    `routing_mode`.

    `neural_backend` (Phase 9) picks "python" (the default reference
    implementation) or "cpp" (the compiled kernel) for whatever neural
    inference `routing_mode` ends up calling for; it does nothing when no
    neural inference happens at all (`routing_mode="heuristic"`, or no
    `router_model`).
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
    eligible_names = {tool.name for tool, _score, _features in ranked}

    neural_scores: dict[str, float] = {}
    fallback_reason: str | None = None
    selected_tool = ranked[0][0]
    model_version_used = "heuristic-v1"

    if routing_mode in ("shadow", "active") and router_model is not None:
        computed = _neural_scores_for(router_model, ranked, neural_backend)
        if computed is None:
            if routing_mode == "active":
                fallback_reason = "feature_version_mismatch_or_inference_error"
        else:
            neural_scores = computed
            if routing_mode == "active":
                neural_top_name = max(neural_scores, key=neural_scores.get)
                neural_top_confidence = neural_scores[neural_top_name]
                if neural_top_name not in eligible_names:
                    fallback_reason = "invalid_candidate"
                elif neural_top_confidence < confidence_threshold:
                    fallback_reason = "low_confidence"
                else:
                    selected_tool = next(t for t, _, _ in ranked if t.name == neural_top_name)
                    model_version_used = router_model.model_version
    elif routing_mode == "active" and router_model is None:
        fallback_reason = "model_missing"

    was_exploration = False
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
            fallback_reason = None  # exploration is why this was picked, not a fallback
            model_version_used = "heuristic-v1"  # exploration isn't the neural model's decision

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
        model_version=model_version_used,
        fallback_reason=fallback_reason,
        neural_scores=neural_scores,
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
