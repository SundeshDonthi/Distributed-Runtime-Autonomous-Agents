from __future__ import annotations

from dataclasses import dataclass

from runtime.routing.history import ObservedStats
from runtime.tools.registry import ToolMetadata, UnknownCapabilityError, tools_for_capability


@dataclass(frozen=True)
class RouterWeights:
    """Tunable weights for Section 15's deterministic baseline formula:

        score = w1*capability_match + w2*reliability - w3*latency - w4*cost

    This is the router's permanent fallback (Section 15A.1: "Neural inference
    is optional at runtime... on timeout, low confidence, missing model, or
    invalid candidate, use the deterministic baseline"), not a throwaway
    step -- a future neural scorer degrades to this, it doesn't replace it.
    """

    capability_match: float = 1.0
    reliability: float = 1.0
    latency: float = 1.0
    cost: float = 1.0


DEFAULT_WEIGHTS = RouterWeights()


def score_candidate(
    tool: ToolMetadata,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    observed: ObservedStats | None = None,
) -> float:
    """`observed`, when available, replaces the tool's static reliability/
    latency priors with what actually happened in recent calls (Section
    15B.3) -- this is the "prior outcomes" half of tool selection. With no
    history yet (cold start, Section 15C.3), it falls back to the fixed
    `ToolMetadata` values. Cost isn't tracked as an observed quantity here:
    unlike reliability/latency it's normally a known, fixed price rather than
    something that varies attempt to attempt.
    """
    # Every candidate here already matches the requested capability (callers
    # only ever score tools returned by `tools_for_capability`), so this term
    # is always 1.0 today -- kept so the formula matches Section 15 exactly,
    # and as the slot a future fuzzy capability-match score would fill.
    capability_match = 1.0
    reliability = observed.success_rate if observed else tool.reliability
    latency = observed.avg_latency_seconds if observed else tool.base_latency_seconds
    return (
        weights.capability_match * capability_match
        + weights.reliability * reliability
        - weights.latency * latency
        - weights.cost * tool.cost_per_call
    )


def rank_candidates_with_scores(
    capability: str,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    stats: dict[str, ObservedStats] | None = None,
) -> list[tuple[ToolMetadata, float]]:
    candidates = tools_for_capability(capability)
    if not candidates:
        raise UnknownCapabilityError(capability)
    stats = stats or {}
    scored = [(t, score_candidate(t, weights, stats.get(t.name))) for t in candidates]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)


def rank_candidates(
    capability: str,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    stats: dict[str, ObservedStats] | None = None,
) -> list[ToolMetadata]:
    return [tool for tool, _ in rank_candidates_with_scores(capability, weights, stats)]


def select_tool(
    capability: str,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    stats: dict[str, ObservedStats] | None = None,
) -> ToolMetadata:
    return rank_candidates(capability, weights, stats)[0]
