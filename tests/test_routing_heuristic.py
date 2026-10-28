from __future__ import annotations

import pytest

from runtime.routing.heuristic import RouterWeights, rank_candidates, score_candidate, select_tool
from runtime.tools.registry import UnknownCapabilityError, ToolMetadata


def _noop(args: dict) -> dict:
    return {}


FAST = ToolMetadata("fast", "demo", _noop, base_latency_seconds=0.05, reliability=0.85, cost_per_call=0.001)
CAREFUL = ToolMetadata("careful", "demo", _noop, base_latency_seconds=0.3, reliability=0.99, cost_per_call=0.01)


def test_score_rewards_reliability_and_penalizes_latency_and_cost():
    fast_score = score_candidate(FAST)
    careful_score = score_candidate(CAREFUL)

    # Equal weights: careful's extra reliability (+0.14) is outweighed by its
    # extra latency (+0.25), so fast scores higher under the defaults.
    assert fast_score > careful_score


def test_weights_can_flip_the_choice_toward_reliability():
    # Heavily penalize unreliability relative to latency/cost and the router
    # should prefer the slower-but-more-reliable tool instead -- proves the
    # formula actually trades these off rather than always picking one.
    reliability_first = RouterWeights(reliability=10.0, latency=1.0, cost=1.0)
    assert score_candidate(CAREFUL, reliability_first) > score_candidate(FAST, reliability_first)


def test_select_tool_picks_the_registered_lookup_winner():
    # Exercises the real registry (lookup_fast / lookup_careful from
    # Section 15's Tool A/B example) rather than the local fixtures above.
    chosen = select_tool("lookup")
    assert chosen.name in {"lookup_fast", "lookup_careful"}
    assert chosen.capability == "lookup"


def test_rank_candidates_orders_by_score_descending():
    ranked = rank_candidates("lookup")
    scores = [score_candidate(t) for t in ranked]
    assert scores == sorted(scores, reverse=True)


def test_unknown_capability_raises():
    with pytest.raises(UnknownCapabilityError):
        select_tool("does_not_exist")
