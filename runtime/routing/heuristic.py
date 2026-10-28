from __future__ import annotations

from dataclasses import dataclass

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


def score_candidate(tool: ToolMetadata, weights: RouterWeights = DEFAULT_WEIGHTS) -> float:
    # Every candidate here already matches the requested capability (callers
    # only ever score tools returned by `tools_for_capability`), so this term
    # is always 1.0 today -- kept so the formula matches Section 15 exactly,
    # and as the slot a future fuzzy capability-match score would fill.
    capability_match = 1.0
    return (
        weights.capability_match * capability_match
        + weights.reliability * tool.reliability
        - weights.latency * tool.base_latency_seconds
        - weights.cost * tool.cost_per_call
    )


def rank_candidates(
    capability: str, weights: RouterWeights = DEFAULT_WEIGHTS
) -> list[ToolMetadata]:
    candidates = tools_for_capability(capability)
    if not candidates:
        raise UnknownCapabilityError(capability)
    return sorted(candidates, key=lambda t: score_candidate(t, weights), reverse=True)


def select_tool(capability: str, weights: RouterWeights = DEFAULT_WEIGHTS) -> ToolMetadata:
    return rank_candidates(capability, weights)[0]
