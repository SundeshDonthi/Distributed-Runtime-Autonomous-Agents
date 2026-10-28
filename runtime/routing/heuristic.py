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

# Bumped whenever the *shape* of the feature vector below changes (a field
# added/removed/renamed) -- Section 15D.2: "version feature extraction... so
# a training run can tell whether an old snapshot still means what it says.
# Deliberately NOT bumped when a weight or scoring formula changes; those are
# captured per-decision via the logged `weights` dict instead.
FEATURE_VERSION = "features-v1"


def build_feature_vector(tool: ToolMetadata, observed: ObservedStats | None = None) -> dict:
    """The exact inputs `score_candidate` uses for one candidate, as a
    structured, versioned snapshot -- kept in the same module as the scorer
    itself so the two can never silently drift apart. Persisted alongside
    each routing candidate at decision time (Section 15D.1's "feature
    snapshot") so a later training run sees precisely what the router saw,
    not today's since-updated observed stats (Section 15D.2: "avoid using
    post-execution fields as pre-execution features").
    """
    return {
        "feature_version": FEATURE_VERSION,
        "capability_match": 1.0,
        "static_reliability": tool.reliability,
        "static_latency_seconds": tool.base_latency_seconds,
        "cost_per_call": tool.cost_per_call,
        "observed_reliability": observed.success_rate if observed else None,
        "observed_latency_seconds": observed.avg_latency_seconds if observed else None,
        "observed_sample_size": observed.sample_size if observed else 0,
    }


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


def rank_candidates_with_details(
    capability: str,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    stats: dict[str, ObservedStats] | None = None,
) -> list[tuple[ToolMetadata, float, dict]]:
    """Like `rank_candidates_with_scores`, plus each candidate's feature
    snapshot -- the one place both the score and the exact inputs it came
    from are computed together, so a persisted snapshot can never disagree
    with the score that was actually used to rank it.
    """
    candidates = tools_for_capability(capability)
    if not candidates:
        raise UnknownCapabilityError(capability)
    stats = stats or {}
    scored = [
        (t, score_candidate(t, weights, stats.get(t.name)), build_feature_vector(t, stats.get(t.name)))
        for t in candidates
    ]
    return sorted(scored, key=lambda triple: triple[1], reverse=True)


def rank_candidates_with_scores(
    capability: str,
    weights: RouterWeights = DEFAULT_WEIGHTS,
    stats: dict[str, ObservedStats] | None = None,
) -> list[tuple[ToolMetadata, float]]:
    return [
        (tool, score)
        for tool, score, _features in rank_candidates_with_details(capability, weights, stats)
    ]


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
